"""
STT Engine 3.0: High-Accuracy Faster-Whisper
Optimized for exact word capture like ChatGPT voice mode.

Key improvements:
- Larger model (small.en) for better accuracy
- Enhanced VAD for clean audio segments
- Better initial prompt for technical terms
- Optimized beam search for accuracy
"""

import numpy as np
import warnings
import time
import os
import re
import requests
WhisperModel = None  # lazy import — loaded only when local STT is used
import multiprocessing

try:
    import torch as _torch
    def _cuda_available() -> bool:
        return _torch.cuda.is_available()
except ImportError:
    _torch = None
    def _cuda_available() -> bool:
        return False

warnings.filterwarnings("ignore", category=UserWarning)

import config

# Global model
model = None
model_name = None

# Default to config setting
DEFAULT_MODEL = config.STT_MODEL
LOCAL_FALLBACK_MODEL = "Systran/faster-distil-whisper-small.en"

# AssemblyAI runtime guards
_assembly_fail_count = 0
_assembly_disabled_until = 0.0

# High-accuracy models use beam_size=3; fast models use beam_size=1
_DISTIL_PREFIX = ("distil-whisper/", "Systran/faster-distil-")


def _beam_size(model_size: str) -> int:
    """Pick optimal beam_size per model.
    Distil models use distillation for accuracy — beam=1 is fast and already accurate.
    Standard small/medium benefit from beam=2 for ~10% accuracy gain.
    tiny/base: beam=1 (fast, accuracy limited by model size not beam).
    """
    if any(model_size.startswith(p) for p in _DISTIL_PREFIX):
        return 1   # distilled accuracy, no beam search needed
    if model_size in {"small.en", "medium.en", "large", "large-v2", "large-v3"}:
        return 2
    return 1       # tiny.en, base.en


def _is_high_accuracy(model_size: str) -> bool:
    return _beam_size(model_size) > 1


def _safe_local_model_name(preferred: str = None) -> str:
    """Return a valid local Whisper model name for fallback paths."""
    _VALID_LOCAL = {
        "tiny.en", "tiny", "base.en", "base", "small.en", "small",
        "medium.en", "medium", "large-v1", "large-v2", "large-v3", "large",
        "distil-large-v2", "distil-medium.en", "distil-small.en",
        "distil-large-v3", "distil-large-v3.5", "large-v3-turbo", "turbo",
        "Systran/faster-distil-whisper-small.en",
    }
    if preferred and preferred in _VALID_LOCAL:
        return preferred
    if DEFAULT_MODEL and DEFAULT_MODEL in _VALID_LOCAL:
        return DEFAULT_MODEL
    return LOCAL_FALLBACK_MODEL


def load_model(model_size=None):
    """Load Faster-Whisper model."""
    global model, model_name

    if model_size is None:
        model_size = _safe_local_model_name(DEFAULT_MODEL)
    else:
        model_size = _safe_local_model_name(model_size)

    device = "cuda" if _cuda_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    if model is None or model_name != model_size:
        global WhisperModel
        if WhisperModel is None:
            from faster_whisper import WhisperModel as _WhisperModel
            WhisperModel = _WhisperModel
        cpu_threads = min(multiprocessing.cpu_count(), 6)
        print(f"  [STT] Loading Faster-Whisper '{model_size}' on {device}/{compute_type} threads={cpu_threads}...")
        try:
            model = WhisperModel(model_size, device=device, compute_type=compute_type, cpu_threads=cpu_threads)
            model_name = model_size
        except Exception as e:
            fallback = LOCAL_FALLBACK_MODEL
            if model_size != fallback:
                print(f"  [STT] Model '{model_size}' failed ({e}); falling back to '{fallback}'")
                model = WhisperModel(fallback, device=device, compute_type=compute_type, cpu_threads=cpu_threads)
                model_name = fallback
            else:
                raise

    return model


# Technical vocabulary for Whisper initial_prompt (helps bias toward correct spellings)
TECH_PROMPT = (
    # ── Python core ─────────────────────────────────────────────────────────
    "Python, decorator, generator, iterator, closure, lambda, GIL, asyncio, "
    "tuple, list, dict, set, *args, **kwargs, comprehension, f-string, "
    "dunder, classmethod, staticmethod, metaclass, dataclass, namedtuple, "
    "defaultdict, OrderedDict, Counter, lru_cache, functools, itertools, "
    "pickle, unpickle, deepcopy, shallow copy, context manager, walrus operator, "
    "type hints, Pydantic, pytest, unittest, virtualenv, pipenv, poetry, "
    "async, await, coroutine, event loop, thread, process, GIL, multiprocessing, "
    # ── Django ───────────────────────────────────────────────────────────────
    "Django, ORM, QuerySet, serializer, makemigrations, migrate, "
    "select_related, prefetch_related, ModelForm, ModelAdmin, "
    "class-based view, function-based view, middleware, signal, "
    "custom user model, CSRF, session, context processor, template tag, "
    "Celery, Redis, Channels, WebSocket, gunicorn, uWSGI, WSGI, "
    # ── Django REST Framework ────────────────────────────────────────────────
    "DRF, APIView, ViewSet, ModelViewSet, GenericAPIView, Router, "
    "HyperlinkedModelSerializer, permission class, throttle, pagination, "
    "filter backend, SimpleJWT, token auth, JWT, CORS, "
    # ── DevOps ───────────────────────────────────────────────────────────────
    "Docker, Kubernetes, kubectl, ConfigMap, Ingress, Deployment, "
    "Dockerfile, docker-compose, Helm, ArgoCD, Jenkins, CI/CD, pipeline, "
    "Ansible, playbook, Terraform, Prometheus, Grafana, Nginx, "
    "AWS, EC2, S3, IAM, EKS, ECS, Route 53, CloudWatch, VPC, "
    "ImagePullBackOff, CrashLoopBackOff, OOMKilled, HPA, pod lifecycle, "
    # ── SRE ─────────────────────────────────────────────────────────────────
    "SRE, SLI, SLO, SLA, error budget, burn rate, toil, golden signals, "
    "incident response, postmortem, runbook, on-call, escalation, "
    "MTTD, MTTR, MTTF, RCA, observability, OpenTelemetry, Jaeger, "
    "Alertmanager, PagerDuty, OpsGenie, Loki, ELK stack, Splunk, "
    # ── Production Support / ITIL ────────────────────────────────────────────
    "ITIL, P1, P2, P3, war room, bridge call, root cause analysis, "
    "change management, CMDB, ServiceNow, Jira, incident, problem, "
    "deadlock, zombie process, swap space, log rotation, "
    # ── Linux / Unix ─────────────────────────────────────────────────────────
    "Linux, UNIX, Bash, systemd, systemctl, journalctl, cron, crontab, "
    "inode, filesystem, chmod, chown, grep, awk, sed, top, htop, "
    "ps, lsof, netstat, ss, iostat, vmstat, strace, tcpdump, "
    "symlink, hard link, POSIX, shebang, heredoc, pipefail, "
    "load average, iowait, context switch, swap, OOM killer, "
    # ── Autosys ──────────────────────────────────────────────────────────────
    "Autosys, JIL, sendevent, autorep, box job, on_hold, on_ice, "
    "force_startjob, killjob, job_type, choke level, CA Workload Automation, "
    "ITRS, Geneos, Control-M, WCC, job scheduler, batch job, "
    # ── Telecom / IMS / SIP / SS7 / Diameter ────────────────────────────────
    "IMS, SIP, SIP protocol, SIP message, INVITE, ACK, BYE, REGISTER, OPTIONS, "
    "SDP, RTP, RTCP, SIP proxy, SIP registrar, SIP UA, SIP trunk, "
    "SS7, ISUP, MTP, SCCP, TCAP, MAP, CAP, signaling, signaling point, "
    "Diameter, Diameter AVP, HSS, PCRF, P-CSCF, S-CSCF, I-CSCF, MGCF, "
    "IMS core, VoLTE, VoIP, codec, G.711, G.729, AMR, SRTP, SIPS, "
    "SIP OPTIONS ping, registration, deregistration, re-INVITE, "
    "Wireshark, pcap, packet capture, call flow, call trace, "
    "telecom, telecommunications, network element, NE, OAM, "
    "fault management, performance management, configuration management, "
    "alarm, KPI, NMS, EMS, BSS, OSS, billing, charging, CDR, "
    "5G, 4G, LTE, NGN, NFV, VNF, SDN, NFVI, MANO, "
    "TCP, UDP, SCTP, TLS, IPsec, NAT traversal, STUN, TURN, ICE."
)


# Pre-compiled corrections (compiled once at module load, not per transcription)
_RAW_CORRECTIONS = {
    "pie thon": "Python", "pie-thon": "Python", "python's": "Python's",
    "pie chart": "Python", "4-by-thon": "Python", "4 by thon": "Python",
    "four by thon": "Python", "by thon": "Python",
    "tupel": "tuple", "topple": "tuple",
    "two pull": "tuple", "deck orator": "decorator", "decorate her": "decorator",
    "it a rater": "iterator", "generate her": "generator",
    "generate trip": "generator", "generator trip": "generator",
    "a sink": "async", "a wait": "await",
    "jango": "Django", "d jango": "Django", "d django": "Django",
    "dd jango": "Django", "ddjango": "Django", "d-jango": "Django",
    "re act": "React",
    "reston": "list and", "western": "list and",
    "rich and coupled": "list and tuple", "rich coupled": "list and tuple",
    "un-out": "and odd", "un out": "and odd",
    "entity puruses": "HTTP statuses", "entity purposes": "HTTP statuses",
    "at their room": "errors", "up there": "errors",
    "cacd": "CI/CD", "ci cd": "CI/CD", "c i c d": "CI/CD",
    "see i see d": "CI/CD", "a w s": "AWS",
    "blueprint": "blue-green",
    # Django-specific misheard
    "meet migrations": "makemigrations", "meetmigrations": "makemigrations",
    "meet migration": "makemigrations", "meat migrations": "makemigrations",
    "make migration": "makemigrations", "make migrations": "makemigrations",
    # *args and **kwargs
    "arcs and kwas": "*args and **kwargs", "arcs and kw arcs": "*args and **kwargs",
    "arcs and kwargs": "*args and **kwargs", "arks and kwargs": "*args and **kwargs",
    "arks and kwas": "*args and **kwargs", "arks and kw arks": "*args and **kwargs",
    "arcs": "*args", "kw arcs": "**kwargs", "kw arks": "**kwargs",
    # Microservices
    "microletic": "microservices", "micro letic": "microservices",
    "microlitic": "microservices", "micro litic": "microservices",
    # JWT (often misheard as GWT)
    "gwt": "JWT", "g w t": "JWT",
    # Django ORM
    "django over m": "Django ORM", "d jango over m": "Django ORM",
    "django orm": "Django ORM",
    # CORS
    "cars error": "CORS error", "cars errors": "CORS errors",
    # Nginx (often misheard as NVIDIA)
    "nvidia architecture engine": "Nginx",
    # Tuple misheard as Docker/other
    "list and docker": "list and tuple", "list and docker in": "list and tuple in",
    "list and darker": "list and tuple", "list and talker": "list and tuple",
    "list and tougher": "list and tuple", "list and topper": "list and tuple",
    # Encapsulation misheard
    "capitation": "encapsulation", "capitulation": "encapsulation",
    "cap station": "encapsulation", "capsulation": "encapsulation",
    "python and capitation": "Python encapsulation",
    "python capitation": "Python encapsulation",
    "python encapsulation": "Python encapsulation",
    # Raw SQL misheard
    "big django sql": "raw Django SQL", "big jango sql": "raw Django SQL",
    "pig django sql": "raw Django SQL",
    "raw sql": "raw SQL", "raw sequel": "raw SQL",
    # Abstraction misheard
    "obstruction": "abstraction", "obstraction": "abstraction",
    # etcd misheard
    "et cd": "etcd", "etc d": "etcd", "e t c d": "etcd",
    "80 cd": "etcd", "at cd": "etcd",
    # Pod lifecycle
    "pod life cycle": "pod lifecycle", "pot lifecycle": "pod lifecycle",
    # "Write" misheard as "Right/Righty" (very common STT error)
    "righty": "write an",
    "right here,": "write a",
    "right here": "write a",
    "right there,": "write a",
    "right there": "write a",
    # "explain" misheard as "ask my"
    "ask my": "explain",
    # Ansible misheard
    "answerable": "Ansible",
    "answer ball": "Ansible",
    "answer world": "Ansible",
    "answer able": "Ansible",
    "ansible's": "Ansible's",
    # Route 53 misheard
    "root 53": "Route 53",
    "road 53": "Route 53",
    "route fifty three": "Route 53",
    "root fifty three": "Route 53",
    "road fifty three": "Route 53",
    "road fifty-three": "Route 53",
    # Pickling misheard
    "trickling": "pickling",
    "trickle": "pickle",
    "un-jickling": "unpickling",
    "un jickling": "unpickling",
    "unjickling": "unpickling",
    "un-pickling": "unpickling",
    "jickling": "pickling",
    "jickle": "pickle",
    # CSV / S3 misheard
    "c-s, v-d": "CSV",
    "cs vd": "CSV",
    # SLI/SLO/SLA misheard
    "sl yet": "SLI",
    "slw": "SLO",
    "slite": "SLA",
    "sl y": "SLI",
    # EKS/ECS misheard
    "e k s": "EKS",
    "e c s": "ECS",
    # AWS X-Ray
    "x-ray and our tools": "profiling and debugging tools",
    "x ray and our tools": "profiling and debugging tools",
    # ConfigMap misheard
    "conflict map": "ConfigMap",
    "config map": "ConfigMap",
    # "Write an Ansible" misheard
    "variety and support": "Write an Ansible",
    "break a ansible": "Write an Ansible",
    "break an ansible": "Write an Ansible",
    "redia ansible": "Write an Ansible",
    # "playable" → "playbook"
    "ansible playable": "Ansible playbook",
    "playable for": "playbook for",
    # "Linux server/machine" misheard
    "linex service": "Linux server",
    "linex server": "Linux server",
    "linex": "Linux",
    # "What is S3" misheard
    "who stands in aws": "What is S3 in AWS",
    "who stands": "What is S3",
    # Django session misheard
    "djing go": "Django",
    "dj go": "Django",
    "dj. go": "Django",
    # OOP misheard as OBS
    "obs concept": "OOP concept",
    "obs concepts": "OOP concepts",
    "obs principle": "OOP principle",
    "obs principles": "OOP principles",
    # Write misheard as Righte / Right
    "righte,": "Write",
    "righte": "Write",
    # Ansible Playbook misheard
    "and so wont play both": "Ansible Playbook",
    "and so won't play both": "Ansible Playbook",
    "and so won't play": "Ansible Playbook",
    "ansible script": "Ansible Playbook",
    "ansiblescript": "Ansible Playbook",
    # X-Wing / explain misheard
    "x-wing about": "explain",
    "x wing about": "explain",
    # Docker Swarm misheard
    "docker spawn": "Docker Swarm",
    "docker spawns": "Docker Swarm",
    # on-premises misheard
    "amp premieres": "on-premises",
    "on premieres": "on-premises",
    "on premise": "on-premises",
    "on prem": "on-premises",
    # "art token" → "auth token"
    "art token": "auth token",
    "art, token": "auth token",
    # distil-small mishears "Write" at sentence start
    "alright, a function": "write a function",
    "right, a function": "write a function",
    "alright a function": "write a function",
    "right a function": "write a function",
    "thank you a function": "write a function",
    "thank you, a function": "write a function",
    "alright, a code": "write a code",
    "right, a code": "write a code",
    "thank you, a code": "write a code",
    "alright, a program": "write a program",
    "right, a program": "write a program",
    "alright, a class": "write a class",
    "right, a class": "write a class",
    "alright, a script": "write a script",
    "right, a script": "write a script",
    "alright, an ansible": "write an ansible",
    "right, an ansible": "write an ansible",
    "alright, a decorator": "write a decorator",
    "right, a decorator": "write a decorator",
    "alright, a generator": "write a generator",
    "right, a generator": "write a generator",
    # monkey patching mishear
    "monkey, man-petching": "monkey patching",
    "man-petching": "monkey patching",
    "monkey man patching": "monkey patching",
    # Decorator misheard
    "the creator": "the decorator",
    "a creator": "a decorator",
    "creator": "decorator",
    "curators": "decorators",
    # GIL misheard
    "my grid": "GIL",
    # manage.py misheard
    "manage.pv": "manage.py",
    # Decorator misheard at word level
    "decatur": "decorator",
    "decor in python": "decorator in Python",
    "what is decor": "what is decorator",
    "what is a decor": "what is a decorator",
    # Ansible new mishears
    "a-aunseboil": "Ansible",
    "aunseboil": "Ansible",
    "aunsoball": "Ansible",
    "aunsobol": "Ansible",
    "ansible play for": "Ansible Playbook for",
    "playbook for insta and": "Playbook for installing",
    # Generator new mishears
    "genrator": "generator",
    "generater": "generator",
    # Polymorphism misheard
    "polymonchism": "Polymorphism",
    "polymorfism": "Polymorphism",
    # YAML file misheard
    "yaml finds": "YAML file",
    "yaml find": "YAML file",
    # List comprehension misheard
    "list to come for": "list comprehension",
    "list to come,": "list comprehension",
    "list to come": "list comprehension",
    # Pickling
    "tickling": "pickling",
    # CAP theorem misheard
    "boot and cap situation": "CAP theorem",
    "cap situation": "CAP theorem",
    "cap theorem": "CAP theorem",
    # "What a signal" → "What is Django signal"
    "what a signal": "what is Django signal",
    # async misheard as "essence"
    "what is essence": "what is async",
    # GIL mishears (Indian accent: "gill" or "jil")
    "what is gill": "what is GIL",
    "what is the gill": "what is the GIL",
    "explain gill": "explain GIL",
    "tell me about gill": "tell me about GIL",
    "gill in python": "GIL in Python",
    "jil in python": "GIL in Python",
    "what is jil": "what is GIL",
    # asyncio mishears
    "what is assync io": "what is asyncio",
    "how does assync io": "how does asyncio",
    "what is a sync io": "what is asyncio",
    "a sync i o": "asyncio",
    "async io": "asyncio",
    # threading mishears
    "multi threading": "multithreading",
    "what is multi threading": "what is multithreading",
    "difference between multi threading": "difference between multithreading",
    # microservice mishears
    "micro service": "microservice",
    "micro services": "microservices",
    # CI/CD mishears
    "cacd": "CI/CD",
    "ci cd": "CI/CD",
    "c i c d": "CI/CD",
    "sea ice d": "CI/CD",
    # Kubernetes mishears
    "cube nettis": "Kubernetes",
    "cube nettles": "Kubernetes",
    "cube net ease": "Kubernetes",
    # Django ORM
    "d jango": "Django",
    "d-jango": "Django",
    # REST API
    "rust api": "REST API",
    "rest a p i": "REST API",
    # API Gateway
    "api gateway": "API Gateway",
    # Deep copy / shallow copy
    "deep coffee": "deep copy",
    "deep coffey": "deep copy",
    "shallow coffee": "shallow copy",
    # Garbage collector
    "garbage collector": "garbage collector",
    "garbase collector": "garbage collector",
    # Connection pooling
    "connection pooling": "connection pooling",
    "connection pulling": "connection pooling",
    # Load balancer
    "load balancer": "load balancer",
    "load balancing": "load balancing",
    # WebSocket
    "web socket": "WebSocket",
    "websockets": "WebSockets",
    # Terraform
    "terra form": "Terraform",
    "terra forms": "Terraform",
    # Prometheus
    "promi thesis": "Prometheus",
    "prom etheus": "Prometheus",
    # Grafana
    "grafana": "Grafana",
    # PostgreSQL mishears
    "postgres ql": "PostgreSQL",
    "post gre sql": "PostgreSQL",
    "post grey sql": "PostgreSQL",
    # Redis
    "read is": "Redis",
    "reeds": "Redis",
    # Celery (task queue)
    "salary": "Celery",
    "sellers": "Celery",
    # gunicorn mishears
    "guni corn": "gunicorn",
    "guni corn": "gunicorn",
    "uni corn": "gunicorn",
    # WSGI
    "wizzy": "WSGI",
    "w s g i": "WSGI",
    # Docker
    "doc car": "Docker",
    "docket": "Docker",
    # Helm
    "helm charts": "Helm charts",
    # ArgoCD
    "argo cd": "ArgoCD",
    "argo c d": "ArgoCD",
    # Concurrency
    "currency": "concurrency",  # "what is currency" → concurrency (interview context)
    # Deadlock
    "dead lock": "deadlock",
    "dead walk": "deadlock",
    # Race condition
    "race condition": "race condition",
    "raise condition": "race condition",
    # Virtual environment
    "virtual environment": "virtual environment",
    "venv": "venv",
    # Lambda function
    "lambda function": "lambda function",
    "lamba": "lambda",
    # Serialization
    "serializaton": "serialization",
    "serialization": "serialization",
    # OOP
    "o o p": "OOP",
    "object oriented programming": "OOP",
    # Inheritance
    "in heritage": "inheritance",
    "inheridence": "inheritance",

    # ── Kubernetes / DevOps mis-transcriptions (Sarvam) ──────────────────────
    # ImagePullBackOff
    "image pullback of":             "ImagePullBackOff",
    "image pullbackoff":             "ImagePullBackOff",
    "image pull back of":            "ImagePullBackOff",
    "image pull back":               "ImagePullBackOff",
    "imagepullback":                 "ImagePullBackOff",
    "image pull-back-off":           "ImagePullBackOff",
    # pod lifecycle
    "part life cycle":               "pod lifecycle",
    "part lifecycle":                "pod lifecycle",
    "part lifescycle":               "pod lifecycle",
    # Autosys
    "autosis":                       "Autosys",
    "auto sys":                      "Autosys",
    "auto sis":                      "Autosys",
    "ottosis":                       "Autosys",
    # HPA / cluster autoscaler
    "hp and coaster auto cycler":    "HPA and cluster autoscaler",
    "coaster auto cycler":           "cluster autoscaler",
    "hp and cluster autoscaler":     "HPA and cluster autoscaler",
    "h p a":                         "HPA",
    "horizontal pod autoscaler":     "HPA",
    "cluster auto cycler":           "cluster autoscaler",
    # CrashLoopBackOff
    "crash loop back off":           "CrashLoopBackOff",
    "crash loop backoff":            "CrashLoopBackOff",
    # OOMKilled
    "o o m killed":                  "OOMKilled",
    "oom killed":                    "OOMKilled",
    # kubectl
    "cube control":                  "kubectl",
    "cube ctl":                      "kubectl",
    "kube ctl":                      "kubectl",
    "kube control":                  "kubectl",
    # Namespace
    "name space":                    "namespace",
    # StatefulSet / DaemonSet
    "state full set":                "StatefulSet",
    "stateful set":                  "StatefulSet",
    "daemon set":                    "DaemonSet",
    "daemon sets":                   "DaemonSets",
    "demon set":                     "DaemonSet",
    "replica set":                   "ReplicaSet",
    # Linux
    "journal ct l":                  "journalctl",
    "journal control":               "journalctl",
    "system d":                      "systemd",
    "sys t e m d":                   "systemd",
    "cron tab":                      "crontab",
    "cron job":                      "CronJob",
    # ── Telecom / IMS / SIP / SS7 / Diameter ────────────────────────────────
    "i m s":                         "IMS",
    "s i p":                         "SIP",
    "s s 7":                         "SS7",
    "s s seven":                     "SS7",
    "s c c p":                       "SCCP",
    "t c a p":                       "TCAP",
    "i s u p":                       "ISUP",
    "m t p":                         "MTP",
    "h s s":                         "HSS",
    "p c r f":                       "PCRF",
    "p c s c f":                     "P-CSCF",
    "s c s c f":                     "S-CSCF",
    "i c s c f":                     "I-CSCF",
    "m g c f":                       "MGCF",
    "vol t e":                       "VoLTE",
    "vo lte":                        "VoLTE",
    "volet":                         "VoLTE",
    "vo i p":                        "VoIP",
    "voice over ip":                 "VoIP",
    "voice over internet protocol":  "VoIP",
    "s d p":                         "SDP",
    "r t p":                         "RTP",
    "r t c p":                       "RTCP",
    "s r t p":                       "SRTP",
    "avp":                           "AVP",
    "diameter avp":                  "Diameter AVP",
    "n f v":                         "NFV",
    "v n f":                         "VNF",
    "s d n":                         "SDN",
    "o a m":                         "OAM",
    "b s s":                         "BSS",
    "o s s":                         "OSS",
    "c d r":                         "CDR",
    "n g n":                         "NGN",
    "k p i":                         "KPI",
    "n m s":                         "NMS",
    "e m s":                         "EMS",
    "n a t":                         "NAT",
    "s c t p":                       "SCTP",
    "ipsec":                         "IPsec",
    "i p sec":                       "IPsec",
    "s t u n":                       "STUN",
    "t u r n":                       "TURN",
}
# Static compiled corrections (from _RAW_CORRECTIONS above)
# stt_learner.py prepends learned corrections to this list at runtime.
_STATIC_COMPILED_CORRECTIONS = [
    (re.compile(re.escape(wrong), re.IGNORECASE), right)
    for wrong, right in _RAW_CORRECTIONS.items()
]

# Runtime list — stt_learner hot-reloads learned corrections into this.
# Starts as a copy of static; learned corrections are prepended on reload.
_COMPILED_CORRECTIONS = list(_STATIC_COMPILED_CORRECTIONS)


def _init_learned_corrections():
    """Load learned corrections from DB at startup (background, non-blocking)."""
    try:
        import stt_learner as _learner
        _learner.reload_into_stt(force=True)
        _learner._start_reload_thread()
        print(f"  [STT] Learned corrections loaded ({len(_COMPILED_CORRECTIONS)} total)")
    except Exception as e:
        print(f"  [STT] Learned corrections load skipped: {e}")


# Load learned corrections in a daemon thread so import doesn't block
import threading as _threading
_threading.Thread(target=_init_learned_corrections, daemon=True, name="stt-init-learned").start()


# Cached Deepgram client — created once, reused on every call
_deepgram_client = None

# Persistent HTTP sessions — reuses TCP connections, avoids 500ms TLS handshake per call
_deepgram_session = None
_sarvam_session = None


def _get_deepgram_session():
    global _deepgram_session
    if _deepgram_session is None:
        _deepgram_session = requests.Session()
        _deepgram_session.headers.update({
            "Authorization": f"Token {config.DEEPGRAM_API_KEY}",
            "Content-Type": "audio/wav",
        })
        # Warm up TLS connection with a valid silent WAV so first real call is fast
        try:
            import io
            import soundfile as _sf
            import numpy as _np
            _buf = io.BytesIO()
            _sf.write(_buf, _np.zeros(1600, dtype=np.float32), 16000, format='WAV', subtype='PCM_16')
            _warmup = _deepgram_session.post(
                "https://api.deepgram.com/v1/listen",
                params={"model": "nova-3", "language": "en"},
                data=_buf.getvalue(),
                timeout=10,
            )
            print(f"  [STT] Deepgram session ready (status={_warmup.status_code})")
        except Exception:
            pass
    return _deepgram_session


def _get_sarvam_session():
    global _sarvam_session
    if _sarvam_session is None:
        from requests.adapters import HTTPAdapter
        _sarvam_session = requests.Session()
        _sarvam_session.headers.update({
            "api-subscription-key": config.SARVAM_API_KEY,
            "Connection": "keep-alive",   # reuse TCP connection between questions
        })
        # Mount adapter with pool_connections=2 so concurrent greenlets share TLS sessions
        adapter = HTTPAdapter(pool_connections=2, pool_maxsize=4)
        _sarvam_session.mount("https://", adapter)
        # Warm up TLS by hitting the root — establishes TCP+TLS before first real STT call
        try:
            _sarvam_session.get("https://api.sarvam.ai/", timeout=5)
        except Exception:
            pass
        print("  [STT] Sarvam session ready (keep-alive)")
    return _sarvam_session

# Technical keyterms to boost recognition accuracy in Deepgram Nova-3
# Focused on: Python · Django · DRF · DevOps · SRE · Linux · Autosys · Production Support
_DEEPGRAM_KEYTERMS = [
    # ── Python core ──────────────────────────────────────────────────────────
    "Python", "decorator", "generator", "iterator", "GIL", "Global Interpreter Lock",
    "tuple", "list", "dict", "set", "list comprehension", "dict comprehension",
    "lambda", "closure", "async", "await", "asyncio", "coroutine", "event loop",
    "*args", "**kwargs", "LEGB", "scope", "monkey patching",
    "pickle", "unpickle", "deepcopy", "shallow copy",
    "metaclass", "dunder", "classmethod", "staticmethod",
    "dataclass", "namedtuple", "defaultdict", "OrderedDict", "Counter",
    "lru_cache", "functools", "itertools", "contextlib",
    "f-string", "walrus operator", "type hints", "Pydantic", "mypy",
    "pytest", "unittest", "mock", "patch", "fixture",
    "virtualenv", "venv", "pipenv", "poetry", "pip",
    "threading", "multiprocessing", "concurrent.futures", "subprocess",
    "encapsulation", "polymorphism", "inheritance", "abstraction",
    "SOLID", "OOP", "mutable", "immutable",
    # ── Django ───────────────────────────────────────────────────────────────
    "Django", "ORM", "QuerySet", "manager", "makemigrations", "migrate",
    "select_related", "prefetch_related", "N+1", "raw SQL",
    "ModelForm", "ModelAdmin", "inline admin",
    "class-based view", "function-based view", "generic view",
    "ListView", "DetailView", "CreateView", "UpdateView", "DeleteView",
    "middleware", "signal", "post_save", "pre_save",
    "custom user model", "AbstractUser", "CSRF", "session", "cookie",
    "context processor", "template tag", "template filter",
    "Celery", "beat", "task", "periodic task", "Redis", "RabbitMQ",
    "Django Channels", "WebSocket", "ASGI", "WSGI",
    "gunicorn", "uWSGI", "Whitenoise", "static files",
    "manage.py", "settings", "INSTALLED_APPS", "SECRET_KEY",
    "SQLAlchemy", "Flask", "FastAPI",
    # ── Django REST Framework ────────────────────────────────────────────────
    "DRF", "APIView", "ViewSet", "ModelViewSet", "ReadOnlyModelViewSet",
    "GenericAPIView", "ListAPIView", "RetrieveAPIView", "CreateAPIView",
    "Router", "DefaultRouter", "SimpleRouter",
    "Serializer", "ModelSerializer", "HyperlinkedModelSerializer",
    "serializer fields", "validated_data", "to_representation",
    "permission class", "IsAuthenticated", "IsAdminUser", "AllowAny",
    "authentication class", "TokenAuthentication", "SessionAuthentication",
    "SimpleJWT", "JWT", "refresh token", "access token",
    "throttle", "ScopedRateThrottle", "AnonRateThrottle", "UserRateThrottle",
    "pagination", "PageNumberPagination", "CursorPagination", "LimitOffsetPagination",
    "filter backend", "DjangoFilterBackend", "SearchFilter", "OrderingFilter",
    "CORS", "django-cors-headers",
    # ── DevOps ───────────────────────────────────────────────────────────────
    "Docker", "Dockerfile", "docker-compose", "Docker Swarm",
    "Kubernetes", "kubectl", "ConfigMap", "Secret", "Ingress",
    "Deployment", "Pod", "Service", "Namespace", "ReplicaSet",
    "StatefulSet", "DaemonSet", "CronJob", "HPA", "VPA",
    "Helm", "ArgoCD", "FluxCD", "GitOps",
    "Ansible", "playbook", "role", "inventory", "ad-hoc",
    "Terraform", "provider", "module", "state", "plan", "apply",
    "Jenkins", "pipeline", "Jenkinsfile", "CI/CD",
    "GitHub Actions", "workflow", "GitLab CI",
    "Nginx", "reverse proxy", "load balancer", "upstream",
    "AWS", "EC2", "S3", "IAM", "EKS", "ECS", "Lambda", "Route 53",
    "CloudWatch", "VPC", "Security Group", "ALB", "NLB",
    "Prometheus", "Grafana", "Alertmanager", "PromQL",
    "ImagePullBackOff", "CrashLoopBackOff", "OOMKilled",
    "etcd", "RBAC", "pod lifecycle", "liveness probe", "readiness probe",
    # ── SRE ──────────────────────────────────────────────────────────────────
    "SRE", "SLI", "SLO", "SLA", "error budget", "burn rate", "toil",
    "golden signals", "latency", "traffic", "errors", "saturation",
    "incident response", "postmortem", "runbook", "on-call",
    "MTTD", "MTTR", "MTTF", "RCA", "root cause analysis",
    "observability", "OpenTelemetry", "Jaeger", "Zipkin", "tracing",
    "ELK", "Elasticsearch", "Logstash", "Kibana", "Loki",
    "Splunk", "Datadog", "PagerDuty", "OpsGenie",
    "alert fatigue", "change window", "blast radius",
    # ── Production Support / ITIL ────────────────────────────────────────────
    "ITIL", "P1", "P2", "P3", "war room", "bridge call",
    "change management", "CMDB", "ServiceNow", "Jira",
    "incident", "problem", "change", "release",
    "escalation", "escalation matrix", "SOP",
    "deadlock", "zombie process", "swap space", "log rotation",
    "memory leak", "CPU spike", "disk full", "OOM killer",
    # ── Linux / Unix ─────────────────────────────────────────────────────────
    "Linux", "UNIX", "Bash", "shell", "systemd", "systemctl", "journalctl",
    "cron", "crontab", "inode", "filesystem", "mount", "umount",
    "chmod", "chown", "chgrp", "sticky bit", "setuid",
    "grep", "awk", "sed", "find", "xargs", "cut", "sort", "uniq",
    "top", "htop", "ps", "kill", "killall", "lsof", "netstat", "ss",
    "iostat", "vmstat", "sar", "strace", "tcpdump", "nmap",
    "df", "du", "lsblk", "fdisk", "fstab", "fsck",
    "load average", "iowait", "context switch", "swap", "OOM",
    "symlink", "hard link", "POSIX", "shebang", "heredoc", "pipefail",
    "ssh", "scp", "rsync", "screen", "tmux", "nohup",
    "rpm", "yum", "dnf", "apt", "dpkg",
    # ── Autosys / Job Scheduling ─────────────────────────────────────────────
    "Autosys", "JIL", "sendevent", "autorep", "autostatd", "autoping",
    "box job", "command job", "file watcher", "FW job",
    "on_hold", "on_ice", "force_startjob", "killjob", "job_type",
    "choke level", "CA Workload Automation", "WCC",
    "ITRS", "Geneos", "Control-M", "job scheduler", "batch job",
    "job flow", "box", "machine", "run calendar",
    # ── General tech ─────────────────────────────────────────────────────────
    "JWT", "CORS", "REST", "GraphQL", "microservices", "monolith",
    "CAP theorem", "ACID", "SQL", "NoSQL", "PostgreSQL", "Redis", "Kafka",
    "Git", "merge", "rebase", "pull request", "cherry-pick",
    "multithreading", "concurrency", "mutex", "semaphore",
]

# Pre-build Deepgram params dict once at module load (avoids per-call list iteration)
# NOTE: keyterms removed — 100+ keyterm params exceed URL length limit (400 Bad Request).
# nova-3 already handles technical terminology accurately without keyterms.
_DEEPGRAM_PARAMS: dict = {
    "model": "nova-3",
    "language": "en",
    "smart_format": "true",
    "punctuate": "true",
    "filler_words": "false",
    "encoding": "linear16",
    "sample_rate": str(getattr(__import__('config'), 'AUDIO_SAMPLE_RATE', 16000)),
}


def _get_deepgram_client():
    """Return cached Deepgram client, creating it once on first call."""
    global _deepgram_client
    if _deepgram_client is None:
        from deepgram import DeepgramClient
        _deepgram_client = DeepgramClient(api_key=config.DEEPGRAM_API_KEY)
        print("  [STT] Deepgram client initialized")
    return _deepgram_client


def _transcribe_deepgram(audio_array: np.ndarray):
    """
    Transcribe using Deepgram Nova-3 REST API with persistent session.
    ~300-600ms latency with connection reuse. Requires DEEPGRAM_API_KEY in .env
    """
    import io
    import soundfile as sf

    if not config.DEEPGRAM_API_KEY:
        print("  [STT] WARNING: DEEPGRAM_API_KEY not set — falling back to local Whisper")
        return _transcribe_local(audio_array)

    buf = io.BytesIO()
    sf.write(buf, audio_array, config.AUDIO_SAMPLE_RATE, format='WAV', subtype='PCM_16')
    audio_bytes = buf.getvalue()

    try:
        session = _get_deepgram_session()
        resp = session.post(
            "https://api.deepgram.com/v1/listen",
            params=_DEEPGRAM_PARAMS,
            data=audio_bytes,
            timeout=8,
        )
        resp.raise_for_status()
        result = resp.json()
        channel = result["results"]["channels"][0]
        alt = channel["alternatives"][0]
        text = alt.get("transcript", "").strip()
        confidence = float(alt.get("confidence", 0.9))
        return text, confidence
    except Exception as e:
        print(f"  [STT] Deepgram error: {e} — falling back to local Whisper")
        return _transcribe_local(audio_array)


def _transcribe_assemblyai(audio_array: np.ndarray):
    """
    Transcribe using AssemblyAI REST API with circuit breaker.
    Falls back to local Whisper on any API/network issue.
    """
    import io
    import soundfile as sf

    global _assembly_fail_count, _assembly_disabled_until

    if time.time() < _assembly_disabled_until:
        return _transcribe_local(audio_array)

    api_key = getattr(config, 'ASSEMBLYAI_API_KEY', None)
    if not api_key:
        print("  [STT] WARNING: ASSEMBLYAI_API_KEY not set — falling back to local Whisper")
        return _transcribe_local(audio_array)

    max_samples = int(6 * config.AUDIO_SAMPLE_RATE)
    if len(audio_array) > max_samples:
        audio_array = audio_array[-max_samples:]

    buf = io.BytesIO()
    sf.write(buf, audio_array, config.AUDIO_SAMPLE_RATE, format='WAV', subtype='PCM_16')
    audio_bytes = buf.getvalue()

    headers = {"authorization": api_key}
    transcript_headers = {"authorization": api_key, "content-type": "application/json"}

    try:
        upload_resp = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            data=audio_bytes,
            timeout=12,
        )
        upload_resp.raise_for_status()
        audio_url = upload_resp.json().get("upload_url", "")
        if not audio_url:
            raise RuntimeError("AssemblyAI upload_url missing")

        speech_model = getattr(config, 'ASSEMBLYAI_SPEECH_MODEL', 'universal')
        force_en     = getattr(config, 'ASSEMBLYAI_FORCE_ENGLISH', True)

        create_resp = requests.post(
            "https://api.assemblyai.com/v2/transcript",
            headers=transcript_headers,
            json={
                "audio_url": audio_url,
                "speech_model": speech_model,
                **({"language_code": "en"} if force_en else {}),
            },
            timeout=12,
        )
        create_resp.raise_for_status()
        transcript_id = create_resp.json().get("id", "")
        if not transcript_id:
            raise RuntimeError("AssemblyAI transcript id missing")

        max_poll   = getattr(config, 'ASSEMBLYAI_MAX_POLL_SECONDS', 8.0)
        poll_interval = getattr(config, 'ASSEMBLYAI_POLL_INTERVAL_SECONDS', 0.3)
        status_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        deadline   = time.time() + float(max_poll)

        while time.time() < deadline:
            poll_resp = requests.get(status_url, headers=headers, timeout=8)
            poll_resp.raise_for_status()
            result = poll_resp.json()
            status = result.get("status", "")
            if status == "completed":
                text = result.get("text", "").strip()
                confidence = float(result.get("confidence", 0.9) or 0.9)
                _assembly_fail_count = 0
                return text, confidence
            if status == "error":
                raise RuntimeError(result.get("error", "AssemblyAI transcription failed"))
            time.sleep(float(poll_interval))

        raise TimeoutError("AssemblyAI transcription polling timed out")
    except Exception as e:
        _assembly_fail_count += 1
        cb_errors  = getattr(config, 'ASSEMBLYAI_CIRCUIT_BREAKER_ERRORS', 3)
        cb_seconds = getattr(config, 'ASSEMBLYAI_CIRCUIT_BREAKER_SECONDS', 60)
        if _assembly_fail_count >= int(cb_errors):
            _assembly_disabled_until = time.time() + float(cb_seconds)
            print(f"  [STT] AssemblyAI circuit breaker ON for {int(cb_seconds)}s")
        print(f"  [STT] AssemblyAI error: {e} — falling back to local Whisper")
        return _transcribe_local(audio_array)


def _transcribe_local(audio_array: np.ndarray, model_override: str = None):
    """Transcribe using local faster-whisper model."""
    global model
    target_model = _safe_local_model_name(model_override or DEFAULT_MODEL)
    if model is None or model_name != target_model:
        load_model(target_model)

    if audio_array.dtype != np.float32:
        audio_array = audio_array.astype(np.float32)

    max_val = np.abs(audio_array).max()
    if max_val > 0:
        audio_array = audio_array / max_val * 0.95

    # 10s cap — enough for any normal interview question including slow speakers.
    # Whisper's internal encoder handles up to 30s; 10s keeps latency bounded.
    max_seconds = max(1.5, float(getattr(config, 'STT_LOCAL_MAX_AUDIO_SECONDS', 10)))
    MAX_SAMPLES = int(max_seconds * config.AUDIO_SAMPLE_RATE)
    if len(audio_array) > MAX_SAMPLES:
        audio_array = audio_array[-MAX_SAMPLES:]

    beam = _beam_size(model_name or DEFAULT_MODEL)

    segments, info = model.transcribe(
        audio_array,
        beam_size=beam,
        best_of=1,
        temperature=0.0,
        word_timestamps=False,
        vad_filter=True,
        vad_parameters=dict(
            # Lower threshold = more sensitive to soft/slow speech
            threshold=0.25,
            # Allow longer silences inside the utterance before splitting
            min_silence_duration_ms=600,
            # Wider padding ensures slow word starts are not clipped
            speech_pad_ms=250,
        ),
        initial_prompt=TECH_PROMPT,
        language="en",
        condition_on_previous_text=False,
        repetition_penalty=1.3,
        no_repeat_ngram_size=3,
    )

    segments = list(segments)
    text_parts = [seg.text.strip() for seg in segments if seg.text.strip()]
    text = " ".join(text_parts).strip()
    text = post_process_transcription(text)

    if not segments:
        return "", 0.0

    avg_logprob = sum(seg.avg_logprob for seg in segments) / len(segments)
    confidence = min(1.0, np.exp(avg_logprob + 1.2))
    return text, confidence


def transcribe(audio_array):
    """
    Transcribe audio. Routes to correct backend based on config.
    Returns (text, confidence)
    """
    if audio_array.dtype != np.float32:
        audio_array = audio_array.astype(np.float32)

    rms = float(np.sqrt(np.mean(audio_array ** 2)))

    # Cloud STTs (Sarvam/Deepgram) use stricter gate to avoid wasting API calls
    # on background noise, notification sounds, or very faint audio
    if config.STT_BACKEND in ("sarvam", "deepgram"):
        if rms < 0.010:
            return "", 0.0
        # Also skip audio shorter than 0.8s — too short to contain a question
        min_samples = int(config.AUDIO_SAMPLE_RATE * 0.8)
        if len(audio_array) < min_samples:
            return "", 0.0
    else:
        if rms < 0.006:
            return "", 0.0

    if config.STT_BACKEND == "deepgram":
        return _transcribe_deepgram(audio_array)
    elif config.STT_BACKEND == "assemblyai":
        return _transcribe_assemblyai(audio_array)
    elif config.STT_BACKEND == "sarvam":
        return _transcribe_sarvam(audio_array)
    else:
        return _transcribe_local(audio_array)


def _sarvam_translate(text: str, source_lang: str, api_key: str) -> str:
    """Translate regional language text to English using Sarvam AI translate API."""
    try:
        resp = requests.post(
            "https://api.sarvam.ai/translate",
            headers={"api-subscription-key": api_key},
            json={
                "input": text,
                "source_language_code": source_lang,
                "target_language_code": "en-IN",
                "speaker_gender": "Male",
                "mode": "formal",
            },
            timeout=8,
        )
        if resp.status_code == 200:
            return resp.json().get("translated_text", text).strip()
    except Exception as e:
        print(f"  [STT] Sarvam translate error: {e}")
    return text


def _transcribe_sarvam(audio_array: np.ndarray):
    """
    Transcribe using Sarvam AI saarika:v2.5 — always auto-detects language.
    If Telugu/Hindi/Tamil/Kannada detected, auto-translates to English.
    ~400-700ms latency. Requires SARVAM_API_KEY from sarvam.ai
    """
    import io
    import soundfile as sf

    api_key = config.SARVAM_API_KEY
    if not api_key:
        print("  [STT] WARNING: SARVAM_API_KEY not set — falling back to local Whisper")
        return _transcribe_local(audio_array)

    buf = io.BytesIO()
    sf.write(buf, audio_array, config.AUDIO_SAMPLE_RATE, format='WAV', subtype='PCM_16')
    audio_bytes = buf.getvalue()

    try:
        requested_lang = (getattr(config, 'SARVAM_LANGUAGE', 'unknown') or "unknown").strip() or "unknown"

        session = _get_sarvam_session()
        response = session.post(
            "https://api.sarvam.ai/speech-to-text",
            files={"file": ("audio.wav", audio_bytes, "audio/wav")},
            data={
                "model": "saarika:v2.5",
                "language_code": requested_lang,
                "with_timestamps": "false",
                "with_disfluencies": "false",
            },
            timeout=6,
        )
        response.raise_for_status()
        result = response.json()
        text = result.get("transcript", "").strip()
        detected_lang = result.get("language_code", "en-IN")

        # Auto-translate non-English only in auto-detect mode
        if requested_lang == "unknown" and detected_lang and detected_lang != "en-IN" and text:
            print(f"  [STT] {detected_lang} detected — translating to English...")
            text = _sarvam_translate(text, detected_lang, api_key)
            print(f"  [STT] Translated: '{text}'")
            # Drop translated filler (e.g. "Hmm", "Okay sir okay sir")
            if not _is_technical_content(text):
                return "", 0.0

        return text, 0.92
    except Exception as e:
        print(f"\n  ⚠⚠ SARVAM FALLBACK: {e}")
        print("  ⚠⚠ Using local Whisper tiny.en — accuracy will be lower!")
        print("  ⚠⚠ Check SARVAM_API_KEY and internet connection.\n")
        return _transcribe_local(audio_array)


# ── Filler / noise filter ──────────────────────────────────────────────────────

_TECH_TOPICS = frozenset({
    "docker","linux","python","sql","kubernetes","aws","autosys","git","api",
    "database","function","class","error","debug","deploy","monitor","incident",
    "command","query","script","server","network","memory","cpu","disk","file",
    "process","thread","async","cache","queue","list","tuple","dict","loop",
    "string","integer","django","flask","react","node","devops","jenkins",
    "ansible","terraform","container","image","volume","pod","service","index",
    "table","join","log","metric","alert","prometheus","grafana","difference",
    "explain","write","between","tell","describe","define","encoding",
    "exception","import","library","module","package","recursion","algorithm",
    "complexity","latency","throughput","concurrency","deadlock","transaction",
    "replication","sharding","cluster","loadbalancer","proxy","ssl","tls",
    "authentication","authorization","token","jwt","oauth","rest","graphql",
    "microservice","monolith","cicd","pipeline","rollback","migration","schema",
    "orm","trigger","cron","systemd","journalctl","firewall","iptables","sudo",
    "chmod","chown","grep","awk","sed","curl","bash","shell","variable","export",
    "helm","kubectl","configmap","ingress","deployment","statefulset",
    "production","outage","p1","p2","runbook","oncall","postmortem",
    "imagepullbackoff","crashloopbackoff","oomkilled","hpa","lifecycle",
    "namespace","daemonset","replicaset","statefulset","probe",
    # Java / Spring (Indian interview essentials)
    "java","spring","hibernate","maven","gradle","junit","mockito",
    "autowired","transactional","controller","repository","entity","bean",
    "ioc","injection","singleton","factory","observer","builder","strategy",
    "interface","abstract","inheritance","polymorphism","encapsulation",
    "generics","lambda","stream","optional","collector","comparator",
    "hashmap","arraylist","linkedlist","concurrenthashmap","executorservice",
    "completablefuture","synchronized","volatile","threadlocal","runnable",
    "callable","futuretask","semaphore","latch","barrier","mutex",
    "garbage","jvm","jit","classloader","bytecode","heap","stacktrace",
    "checked","unchecked","nullpointer","stackoverflow","outofmemory",
    "kafka","zookeeper","consumer","producer","topic","partition","offset",
    "circuit","gateway","saga","cqrs","eureka","feign","ribbon","zipkin",
    "solid","design","pattern","creational","structural","behavioral",
    "lazy","eager","session","criteria","mapping","cache","n+1",
    "pom","gradle","artifact","dependency","scope","lifecycle",
})

_FILLER_EXACT = frozenset({
    "i am showing a score", "sir i am showing", "sir i am showing a score",
    "okay sir", "ok sir", "hmm", "yes sir", "no sir", "i am ready",
    "please go ahead", "thank you sir", "thank you", "got it sir", "got it",
    "showing a score", "what will you do for me",
    "what will you do for me once",
})


def _is_technical_content(text: str) -> bool:
    """Return False for translated filler/noise that is not a real question."""
    if not text:
        return False
    words = text.lower().split()
    if len(words) < 2:
        return False
    unique = set(words)
    if len(words) > 3 and len(unique) <= 3:
        return False
    lower = text.lower().strip().rstrip("?.!")
    if lower in _FILLER_EXACT:
        return False
    stripped = {w.strip("?.,!") for w in words}
    if stripped & _TECH_TOPICS:
        return True
    if len(words) <= 6:
        social = {"sir","okay","ok","hmm","yes","no","i","you","we","what","how","why"}
        if words[0].strip("?,") in social:
            return False
    return True


def post_process_transcription(text):
    """Fix common transcription errors for technical terms."""
    if not text:
        return text
    result = text
    for pattern, right in _COMPILED_CORRECTIONS:
        result = pattern.sub(right, result)
    return result


def get_model_info():
    if config.STT_BACKEND == "deepgram":
        return {
            'name': 'nova-3',
            'backend': 'deepgram',
            'device': 'cloud',
            'accuracy_mode': 'high'
        }
    if config.STT_BACKEND == "assemblyai":
        return {
            'name': 'assemblyai-universal',
            'backend': 'assemblyai',
            'device': 'cloud',
            'accuracy_mode': 'high'
        }
    if config.STT_BACKEND == "sarvam":
        return {
            'name': 'saarika:v2.5',
            'backend': 'sarvam',
            'device': 'cloud',
            'accuracy_mode': 'high (Indian languages)'
        }
    return {
        'name': model_name,
        'backend': 'faster-whisper',
        'device': 'gpu' if _cuda_available() else 'cpu',
        'accuracy_mode': 'high'
    }
