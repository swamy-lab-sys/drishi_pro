import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer, CountVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import threading
import time

# ── Tuning constants ──────────────────────────────────────────────────────────
SIMILARITY_THRESHOLD  = 0.68   # Reject TF-IDF matches below this score
MAX_PREDICTION_CACHE  = 20     # Hard cap; LRU eviction on overflow
PREDICTION_TTL        = 600    # Optional 1: 10-minute TTL on prediction cache entries
GENERIC_SKILL_CONTEXT = "Draw on your strongest technical experience with concrete examples."


class SemanticEngineV7:
    def __init__(self):
        # Ultra-lightweight vectorizers — no heavy embeddings (Feature 6)
        self.ngram_vec = TfidfVectorizer(stop_words='english', ngram_range=(1, 2))
        self.key_vec   = CountVectorizer(stop_words='english')

        # 3-Tier Answer Sources (Feature 4)
        self.prediction_cache = {}  # topic_node -> {answer, confidence, ts}
        self.semantic_cache   = {}  # q_norm    -> {answer, intent, usage_count, confidence, ts}
        self.prepared_answers = []  # User-curated / Self-promoted answers

        # Skill Graph (Feature 2)
        self.skill_graph = {
            "LINUX":      {"node": "Linux Administration",  "strength": 0.90, "highlights": ["Journalctl", "Systemd", "LVM"]},
            "PYTHON":     {"node": "Backend Development",   "strength": 0.95, "highlights": ["Asyncio", "Decorators", "Flask"]},
            "DOCKER":     {"node": "Containerization",      "strength": 0.80, "highlights": ["Multi-stage builds", "Compose"]},
            "AWS":        {"node": "Cloud Infrastructure",  "strength": 0.75, "highlights": ["EC2", "S3", "IAM"]},
            "KUBERNETES": {"node": "Orchestration",         "strength": 0.70, "highlights": ["Pods", "Services", "Helm"]},
            "DJANGO":     {"node": "Web Framework",         "strength": 0.85, "highlights": ["ORM", "DRF", "Migrations"]},
            "AUTOSYS":    {"node": "Job Scheduling",        "strength": 0.88, "highlights": ["JIL", "sendevent", "job dependencies"]},
            "SQL":        {"node": "Database Engineering",  "strength": 0.85, "highlights": ["Indexing", "Execution plans", "Transactions"]},
            "MONITORING": {"node": "Observability",         "strength": 0.80, "highlights": ["Grafana", "Prometheus", "alerting"]},
            "INCIDENT":   {"node": "Incident Management",   "strength": 0.85, "highlights": ["P1 triage", "RCA", "runbooks"]},
            "PRODUCTION": {"node": "Production Support",    "strength": 0.88, "highlights": ["On-call", "rollback", "post-mortem"]},
            "PODMAN":     {"node": "Containerization",      "strength": 0.75, "highlights": ["Rootless containers", "pod creation", "Compose"]},
        }

        # Topic Graph for next-question prediction (Feature 5)
        self.topic_map = {
            "LINUX":      ["LOGS", "SYSTEMD", "CRON", "PERFORMANCE"],
            "PYTHON":     ["FLASK", "ASYNC", "MEMORY", "GIL"],
            "DOCKER":     ["NETWORKING", "VOLUMES", "IMAGES"],
            "AWS":        ["EC2", "S3", "IAM", "VPC"],
            "KUBERNETES": ["PODS", "SERVICES", "DEPLOYMENTS", "INGRESS"],
            "DJANGO":     ["ORM", "MIGRATIONS", "SIGNALS", "MIDDLEWARE"],
        }

        self.current_topic = "GENERAL"
        self._lock = threading.Lock()

        # Optional 3: Pipeline Health Counters
        self.stats = {
            "prediction_hits": 0,
            "semantic_hits":   0,   # prepared TF-IDF hits
            "runtime_hits":    0,   # runtime_cache hits
            "llm_calls":       0,
            "total":           0,
        }

    # ── Overlay helper ────────────────────────────────────────────────────────

    @staticmethod
    def _overlay(text: str) -> str:
        """Enforce bullet/word-count overlay rules on any cache-returned answer."""
        try:
            from llm_client import humanize_response
            return humanize_response(text)
        except Exception:
            return text

    # ── Index management ──────────────────────────────────────────────────────

    def update_indexes(self, prepared_data):
        """Rebuild TF-IDF index from prepared Q&A data."""
        with self._lock:
            self.prepared_answers = prepared_data
            qs = [p['question'] for p in prepared_data]
            if qs:
                self.ngram_matrix = self.ngram_vec.fit_transform(qs)

    # ── Feature 4: Tiered fast lookup ─────────────────────────────────────────

    def fast_lookup(self, query: str, intent: str = None):
        """Priority: Prediction Cache → Runtime Cache → TF-IDF Prepared.
        Optional 1: TTL expiry on prediction cache.
        Optional 1: Intent guard on runtime cache.
        Returns (answer, confidence, source) or None.
        """
        self.stats["total"] += 1

        # Tier 1: Prediction Cache — keyword match, with TTL check
        now = time.time()
        expired_keys = [k for k, v in self.prediction_cache.items()
                        if now - v['ts'] > PREDICTION_TTL]
        for k in expired_keys:
            del self.prediction_cache[k]  # Optional 2: evict stale predictions

        for node, data in self.prediction_cache.items():
            if node in query.upper():
                self.stats["prediction_hits"] += 1
                answer = self._overlay(data['answer'])
                return answer, data.get('confidence', 0.95), "prediction"

        # Tier 2: Runtime Semantic Cache — exact key + Optional 1: intent guard
        q_norm = query.lower().strip()
        if q_norm in self.semantic_cache:
            entry = self.semantic_cache[q_norm]
            cached_intent = entry.get('intent')
            # Optional 1: Only return if intent matches or one side has no intent stored
            if intent is None or cached_intent is None or intent == cached_intent:
                entry['usage_count'] += 1
                self.stats["runtime_hits"] += 1
                answer = self._overlay(entry['answer'])
                return answer, entry['confidence'], "runtime_cache"

        # Tier 3: TF-IDF Prepared Answers — cosine similarity
        with self._lock:
            if not hasattr(self, 'ngram_matrix') or not self.prepared_answers:
                return None
            q_vec = self.ngram_vec.transform([query])
            sims  = cosine_similarity(q_vec, self.ngram_matrix).flatten()
            idx   = int(np.argmax(sims))
            score = float(sims[idx])
            if score < SIMILARITY_THRESHOLD:
                return None
            self.stats["semantic_hits"] += 1
            answer = self._overlay(self.prepared_answers[idx]['prepared_answer'])
            return answer, score, "semantic"

    # ── Feature 2: Skill-steered context ──────────────────────────────────────

    def get_steered_context(self, query: str) -> str:
        """Guide LLM toward strongest matching skill node.
        Returns generic context when best match strength < 0.5.
        """
        q = query.upper()
        best_node    = None
        max_strength = 0.0
        for skill, data in self.skill_graph.items():
            if skill in q and data['strength'] > max_strength:
                best_node    = data
                max_strength = data['strength']

        if best_node and max_strength >= 0.5:
            return (f"Steer toward: {best_node['node']}. "
                    f"Mention: {', '.join(best_node['highlights'])}.")
        if best_node:
            return GENERIC_SKILL_CONTEXT
        return ""

    # ── Feature 5: Topic detection + prediction ───────────────────────────────

    def detect_topic(self, query: str) -> str:
        """Detect primary topic from query and update current_topic."""
        q = query.upper()
        for topic in self.topic_map:
            if topic in q:
                self.current_topic = topic
                return topic
        return self.current_topic

    def predict_next_topics(self) -> list:
        """Return top-3 predicted follow-up topics for the current topic."""
        return self.topic_map.get(self.current_topic, ["GENERAL", "EXPERIENCE", "PROJECTS"])[:3]

    def predict_and_precompute(self, topic: str) -> list:
        """Return top-3 follow-up topics for a given topic."""
        return self.topic_map.get(topic.upper(), ["GENERAL", "EXPERIENCE", "PROJECTS"])[:3]

    # ── Feature 7: Confidence meter ───────────────────────────────────────────

    def calculate_confidence(self, stt_conf: float, semantic_sim: float,
                              has_skill_match: bool, has_context: bool) -> float:
        """Formula: 0.4·STT + 0.3·Semantic + 0.2·SkillGraph + 0.1·Context. Clamped [0,1]."""
        skill_score   = 1.0 if has_skill_match else 0.3
        context_score = 1.0 if has_context    else 0.3
        raw = (0.4 * stt_conf) + (0.3 * semantic_sim) + (0.2 * skill_score) + (0.1 * context_score)
        return round(max(0.0, min(1.0, raw)), 4)

    # ── Prediction cache store with LRU eviction ──────────────────────────────

    def store_prediction(self, topic: str, answer: str, confidence: float = 0.95):
        """Store precomputed answer.
        Improvement 2: Deduplication — skip if a fresh entry already exists.
        Improvement 1: LRU eviction — remove oldest entries beyond MAX_PREDICTION_CACHE.
        """
        key = topic.upper()

        # Improvement 2: Only overwrite if entry is missing or has expired
        existing = self.prediction_cache.get(key)
        if existing and time.time() - existing['ts'] < PREDICTION_TTL:
            return  # Still fresh — no duplicate write needed

        self.prediction_cache[key] = {
            'answer': answer, 'confidence': confidence, 'ts': time.time()
        }

        # Improvement 1: Hard limit guard — evict least-recently-added entries
        if len(self.prediction_cache) > MAX_PREDICTION_CACHE:
            overflow    = len(self.prediction_cache) - MAX_PREDICTION_CACHE
            oldest_keys = sorted(self.prediction_cache, key=lambda k: self.prediction_cache[k]['ts'])
            for k in oldest_keys[:overflow]:
                del self.prediction_cache[k]

    # ── Optional 3: Stats helpers ─────────────────────────────────────────────

    def increment_llm_calls(self):
        self.stats["llm_calls"] += 1

    def get_stats(self) -> dict:
        """Return pipeline health counters with hit-rate percentages."""
        total = self.stats["total"] or 1  # avoid div-by-zero
        s = dict(self.stats)
        s["prediction_hit_rate"] = round(s["prediction_hits"] / total * 100, 1)
        s["semantic_hit_rate"]   = round((s["semantic_hits"] + s["runtime_hits"]) / total * 100, 1)
        s["llm_usage_rate"]      = round(s["llm_calls"] / total * 100, 1)
        return s

    # ── Feature 3: Self-training promotion ────────────────────────────────────

    def promote_learning(self, q: str, a: str, conf: float, intent: str = None):
        """Track answer re-use. When conf > 0.90 AND usage >= 3, promote to prepared tier."""
        if not q or not a:
            return

        q_norm = q.lower().strip()

        if q_norm not in self.semantic_cache:
            self.semantic_cache[q_norm] = {
                'answer': a, 'intent': intent, 'confidence': conf,
                'usage_count': 1, 'ts': time.time()
            }
            return

        entry = self.semantic_cache[q_norm]
        entry['usage_count'] += 1
        if conf > entry['confidence']:
            entry['answer']     = a
            entry['confidence'] = conf
            entry['intent']     = intent  # Update intent with latest

        if entry['confidence'] > 0.90 and entry['usage_count'] >= 3:
            already = any(p.get('question', '').lower().strip() == q_norm
                         for p in self.prepared_answers)
            if not already:
                self.prepared_answers.append({'question': q, 'prepared_answer': a})
                qs = [p['question'] for p in self.prepared_answers]
                with self._lock:
                    self.ngram_matrix = self.ngram_vec.fit_transform(qs)
            del self.semantic_cache[q_norm]


# Global instance
engine = SemanticEngineV7()
