"""
User Manager for Drishi Pro

Handles:
- Introduction question detection (skip DB/LLM, return stored self_introduction)
- PDF resume text extraction (pypdf → pdfplumber → plain-text fallback)
- Resume summarization (skills, tools, experience) — pure regex, no LLM call
- Active user context loading for LLM prompt injection
- Role-specific answer style hints
"""

import re
from typing import Optional, Dict, Tuple


# ── Introduction Question Detection ──────────────────────────────────────────

_INTRO_PATTERNS = [
    re.compile(r'\bintroduce\s+yourself\b', re.IGNORECASE),
    re.compile(r'\btell\s+(me\s+)?about\s+yourself\b', re.IGNORECASE),
    re.compile(r'\bbrief\s+(introduction|intro)\b', re.IGNORECASE),
    re.compile(r'\bgive\s+(a\s+)?(brief\s+)?intro(duction)?\b', re.IGNORECASE),
    re.compile(r'\bself[\s\-]?introduction\b', re.IGNORECASE),
    re.compile(r'\bwalk\s+me\s+through\s+your\s+(background|resume|profile|experience)\b', re.IGNORECASE),
    re.compile(r'\babout\s+yourself\b', re.IGNORECASE),
    re.compile(r'\bwho\s+are\s+you\b', re.IGNORECASE),
    re.compile(r'\byourself\s+briefly\b', re.IGNORECASE),
    re.compile(r'\bstart\s+with\s+an?\s+introduction\b', re.IGNORECASE),
]


def is_introduction_question(question: str) -> bool:
    """Return True if the question is asking the candidate to introduce themselves."""
    q = question.strip()
    return any(p.search(q) for p in _INTRO_PATTERNS)


# ── PDF Text Extraction ────────────────────────────────────────────────────────

def extract_pdf_text(file_path: str) -> str:
    """
    Extract plain text from a PDF file.
    Tries pypdf first, then pdfplumber, then reads as plain text fallback.
    Handles both real PDFs and .txt files renamed to .pdf.
    """
    path = str(file_path)

    # 1. Try pypdf (lightweight, pure Python)
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                parts.append(text)
        result = '\n'.join(parts).strip()
        if result:
            return result
    except ImportError:
        pass
    except Exception:
        pass

    # 2. Try pdfplumber (better layout extraction)
    try:
        import pdfplumber
        parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
        result = '\n'.join(parts).strip()
        if result:
            return result
    except ImportError:
        pass
    except Exception:
        pass

    # 3. Try pdftotext (system binary)
    try:
        import subprocess
        result = subprocess.run(
            ['pdftotext', path, '-'],
            capture_output=True, text=True, timeout=10
        )
        text = result.stdout.strip()
        if text:
            return text
    except Exception:
        pass

    # 4. Final fallback: read as plain text (for .txt files uploaded as .pdf)
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read().strip()
    except Exception:
        return ''


# ── Resume Summarization ───────────────────────────────────────────────────────

_SKILL_PATTERNS = {
    'languages': re.compile(
        r'\b(python|java(?:script)?|typescript|golang|go\b|rust|c\+\+|c#|ruby|php|'
        r'scala|kotlin|swift|matlab|bash|shell|powershell|perl|lua|sql)\b',
        re.IGNORECASE
    ),
    'frameworks': re.compile(
        r'\b(django|flask|fastapi|spring|react|angular|vue\.?js|node\.?js|express|'
        r'rails|laravel|tensorflow|pytorch|pandas|numpy|scikit[\-\s]?learn|celery|'
        r'gunicorn|uvicorn|pydantic|sqlalchemy|aiohttp|tornado)\b',
        re.IGNORECASE
    ),
    'devops': re.compile(
        r'\b(docker|kubernetes|k8s|helm|terraform|ansible|jenkins|gitlab[\s\-]?ci|'
        r'github[\s\-]?actions|circleci|argocd|prometheus|grafana|elk|elasticsearch|'
        r'logstash|kibana|datadog|splunk|nagios|zabbix|pagerduty|opsgenie|'
        r'puppet|chef|saltstack|vagrant|nexus|sonarqube|artifactory)\b',
        re.IGNORECASE
    ),
    'cloud': re.compile(
        r'\b(aws|azure|gcp|google[\s\-]cloud|ec2|s3|rds|lambda|eks|aks|gke|'
        r'cloudformation|bigquery|cloud[\s\-]run|fargate|iam|vpc|route53|'
        r'cloudwatch|sns|sqs|dynamodb)\b',
        re.IGNORECASE
    ),
    'databases': re.compile(
        r'\b(mysql|postgresql|postgres|oracle|sql[\s\-]?server|mongodb|redis|'
        r'cassandra|dynamodb|neo4j|sqlite|mariadb|influxdb|hive|snowflake|'
        r'clickhouse|druid)\b',
        re.IGNORECASE
    ),
    'os_infra': re.compile(
        r'\b(linux|unix|centos|ubuntu|rhel|debian|fedora|windows[\s\-]?server|'
        r'nginx|apache|tomcat|haproxy|keepalived|'
        # OpenStack core services
        r'openstack|nova|neutron|cinder|glance|keystone|swift|heat|horizon|octavia|'
        r'kolla|kolla[\s\-]ansible|'
        # Virtualization
        r'kvm|qemu|libvirt|xen|vmware|esxi|vsphere|hyper[\s\-]?v|'
        # Storage
        r'ceph|lvm|nfs|iscsi|san|nas|glusterfs|'
        # Network
        r'openvswitch|ovs|vxlan|vlan|bgp|ospf|'
        # Linux tools
        r'systemd|iptables|firewalld|selinux|apparmor|'
        r'grub|grub2|lvm2|xfs|ext4|btrfs|'
        r'strace|tcpdump|wireshark|netstat|ss|nmap|nftables)\b',
        re.IGNORECASE
    ),
    'unix_tools': re.compile(
        r'\b(grep|awk|sed|xargs|find|sort|uniq|cut|tee|tr|wc|'
        r'tar|gzip|bzip2|xz|rsync|scp|ssh|sftp|curl|wget|'
        r'systemctl|journalctl|crontab|logrotate|'
        r'top|htop|iotop|sar|vmstat|iostat|dstat|'
        r'lsof|strace|ltrace|perf|'
        r'df|du|fdisk|parted|lsblk|blkid|mount|'
        r'ps|kill|pkill|pgrep|nohup|screen|tmux)\b',
        re.IGNORECASE
    ),
}

_EXP_PHRASES = re.compile(
    r'\b(production[\s\-]?support|incident[\s\-]?management|on[\s\-]?call|sre|devops|'
    r'ci[\s/\-]?cd|log[\s\-]?analysis|monitoring|automation|microservices|'
    r'container[\s\-]?orchestration|api[\s\-]?development|backend[\s\-]?development|'
    r'full[\s\-]?stack|data[\s\-]?pipeline|infrastructure[\s\-]?automation|'
    r'cloud[\s\-]?migration|database[\s\-]?administration|performance[\s\-]?tuning|'
    r'load[\s\-]?balancing|high[\s\-]?availability|disaster[\s\-]?recovery|'
    r'root[\s\-]?cause[\s\-]?analysis|change[\s\-]?management|'
    # OpenStack/Linux specific experience phrases
    r'openstack[\s\-]?administration|openstack[\s\-]?operations|openstack[\s\-]?support|'
    r'linux[\s\-]?administration|linux[\s\-]?support|unix[\s\-]?administration|'
    r'virtualization[\s\-]?support|vm[\s\-]?management|compute[\s\-]?management|'
    r'network[\s\-]?troubleshooting|storage[\s\-]?management|'
    r'shell[\s\-]?scripting|bash[\s\-]?scripting|automation[\s\-]?scripting|'
    r'server[\s\-]?maintenance|patch[\s\-]?management|capacity[\s\-]?planning|'
    r'live[\s\-]?migration|vm[\s\-]?migration|system[\s\-]?hardening)\b',
    re.IGNORECASE
)


def summarize_resume(text: str) -> str:
    """
    Extract a structured summary from resume text using regex (no LLM call).
    Output is kept concise (<400 chars) for safe LLM context injection.

    Returns multi-line string:
        Skills: Python, Linux, SQL
        Tools: Docker, Jenkins, Kubernetes
        Experience: production support, log analysis

    Returns '' if nothing useful found.
    """
    if not text or len(text.strip()) < 50:
        return ''

    found = {cat: [] for cat in _SKILL_PATTERNS}
    seen: set = set()

    for cat, pattern in _SKILL_PATTERNS.items():
        for m in pattern.finditer(text):
            val = m.group(0).strip().lower()
            if val not in seen:
                seen.add(val)
                found[cat].append(m.group(0).strip())

    parts = []

    # Skills: languages + frameworks + OS/infra
    skills = (
        found['languages'][:6]
        + found['frameworks'][:4]
        + found['os_infra'][:4]
    )
    if skills:
        parts.append('Skills: ' + ', '.join(dict.fromkeys(skills)))

    # Tools: devops + cloud + databases + unix tools
    tools = (
        found['devops'][:5]
        + found['cloud'][:3]
        + found['databases'][:3]
        + found.get('unix_tools', [])[:4]
    )
    if tools:
        parts.append('Tools: ' + ', '.join(dict.fromkeys(tools)))

    # Experience phrases
    exp = []
    for m in _EXP_PHRASES.finditer(text):
        phrase = m.group(0).strip()
        if phrase.lower() not in {p.lower() for p in exp}:
            exp.append(phrase)
    if exp:
        parts.append('Experience: ' + ', '.join(exp[:5]))

    return '\n'.join(parts)


# ── Role Style Hints ──────────────────────────────────────────────────────────

def get_role_style_hint(role: str) -> str:
    """
    Return an answer-style instruction string based on the candidate's role.
    Used to guide the LLM focus when injected into the system prompt.
    """
    if not role:
        return ''
    r = role.lower()

    if any(k in r for k in ('production support', 'prod support', 'support engineer', 'l2', 'l3',
                             'l1 support', 'l2 support', 'l3 support', 'system administrator',
                             'sysadmin', 'linux admin', 'unix admin', 'infrastructure support')):
        return (
            'Answer style: Focus on Linux/Unix commands, log analysis (grep/awk/sed), '
            'monitoring tools (top/htop/vmstat/netstat), systemd/journalctl, '
            'incident response steps, and automation scripts. '
            'Prefer command-line answers over code. Show actual commands in backticks.'
        )
    if any(k in r for k in ('openstack', 'cloud infrastructure', 'cloud operations', 'cloud support',
                             'cloud admin', 'openstack admin', 'openstack support')):
        return (
            'Answer style: Focus on OpenStack services (Nova/Neutron/Cinder/Glance/Keystone), '
            'VM lifecycle management, live migration, KVM/QEMU, OVS networking, '
            'Ceph storage, and production OpenStack troubleshooting workflows.'
        )
    if any(k in r for k in ('devops', 'platform engineer', 'infra engineer', 'release engineer')):
        return (
            'Answer style: Emphasize CI/CD pipelines, Docker, Kubernetes, '
            'infrastructure-as-code (Ansible/Terraform), and observability stack. '
            'Show Jenkinsfile/YAML snippets where relevant.'
        )
    if any(k in r for k in ('sre', 'site reliability', 'reliability engineer')):
        return (
            'Answer style: Focus on SLO/SLI/error budgets, the four golden signals, '
            'on-call incident response, Prometheus/Grafana, and toil reduction. '
            'Quantify availability and latency targets where possible.'
        )
    if any(k in r for k in ('java developer', 'java engineer', 'j2ee', 'spring developer')):
        return (
            'Answer style: Focus on JVM internals, garbage collection, Spring Boot, '
            'Java concurrency (ThreadPool, CompletableFuture), design patterns, '
            'and JPA/Hibernate. Prefer Java code examples.'
        )
    if any(k in r for k in ('python developer', 'python engineer', 'backend developer', 'backend engineer',
                             'software engineer', 'backend', 'full stack', 'full-stack')):
        return (
            'Answer style: Focus on Python internals (GIL, generators, decorators, asyncio, closures), '
            'Django ORM/QuerySet patterns, Django REST Framework (DRF ViewSets, serializers, JWT auth), '
            'Celery task queues, Redis caching, and API best practices. '
            'Include short inline code examples for concept questions.'
        )
    if any(k in r for k in ('autosys', 'batch', 'etl', 'job scheduling', 'workload automation',
                             'batch support', 'etl engineer', 'batch engineer')):
        return (
            'Answer style: Focus on Autosys JIL, job states, sendevent commands, '
            'box job dependencies, and production batch failure recovery procedures. '
            'Show actual Autosys commands in backticks.'
        )
    if any(k in r for k in ('data engineer', 'data scientist', 'ml engineer', 'analytics', 'bi engineer')):
        return (
            'Answer style: Emphasize data pipelines, SQL optimization, ETL workflows, '
            'Python data libraries (Pandas/PySpark), and large-scale data processing.'
        )
    if any(k in r for k in ('cloud engineer', 'cloud architect', 'cloud', 'aws', 'azure', 'gcp')):
        return (
            'Answer style: Focus on cloud services (compute/storage/networking/IAM), '
            'managed infrastructure, cost optimization, and cloud-native architectures.'
        )
    return ''


# ── Active User Context ────────────────────────────────────────────────────────

def get_active_user_context() -> Tuple[str, str, str]:
    """
    Get (resume_summary, role, job_description) for the active user.

    Priority:
    1. Active user profile from state.get_selected_user()
    2. Global ~/.drishi/uploaded_resume.txt + job_description.txt fallback
    3. Empty strings

    Returns:
        (resume_summary, role, job_description) — all strings, safe for LLM injection
    """
    try:
        import state
        user = state.get_selected_user()
    except Exception:
        user = None

    if user:
        resume_text = (user.get('resume_text') or '').strip()
        role = (user.get('role') or '').strip()
        jd = (user.get('job_description') or '').strip()

        # Summarize full text; use short text as-is
        resume_summary = summarize_resume(resume_text) if len(resume_text) > 400 else resume_text

        # Cap JD length for prompt safety
        if len(jd) > 400:
            jd = jd[:400].rsplit(' ', 1)[0] + '...'

        return resume_summary, role, jd

    # Fallback: global uploaded resume
    resume_summary = ''
    try:
        from pathlib import Path
        rp = Path.home() / '.drishi' / 'uploaded_resume.txt'
        if rp.exists():
            text = rp.read_text(encoding='utf-8', errors='ignore')
            resume_summary = summarize_resume(text) if len(text) > 100 else text
    except Exception:
        pass

    jd = ''
    try:
        import config
        from pathlib import Path
        jdp = Path(config.JD_PATH)
        if jdp.exists():
            jd = jdp.read_text(encoding='utf-8', errors='ignore')[:400]
    except Exception:
        pass

    return resume_summary, '', jd


def build_resume_context_for_llm(user: Optional[Dict] = None) -> str:
    """
    Build a concise context block for injection into LLM system prompt.
    Kept under 450 chars total to minimise token overhead while preserving speed.

    Includes:
    - Candidate role
    - Role-specific answer-style hint
    - Resume summary (skills + tools + experience)
    - Job description (truncated)
    - Experience authenticity reminder

    Returns '' when no useful context is available (no active user, no resume).
    """
    if user is None:
        try:
            import state
            user = state.get_selected_user()
        except Exception:
            user = None

    if not user:
        return ''

    lines = []

    role = (user.get('role') or '').strip()
    if role:
        lines.append(f'Candidate Role: {role}')

    style = get_role_style_hint(role)
    if style:
        lines.append(style)

    resume_text = (user.get('resume_text') or '').strip()
    if resume_text:
        summary = summarize_resume(resume_text) if len(resume_text) > 400 else resume_text
        if summary:
            lines.append(f'Resume summary:\n{summary}')

    jd = (user.get('job_description') or '').strip()
    if jd:
        jd_short = jd[:300].rsplit(' ', 1)[0] + ('...' if len(jd) > 300 else '')
        lines.append(f'Job requires: {jd_short}')

    exp_years = user.get('experience_years', 0) or 0
    if exp_years:
        lines.append(f'Experience: {exp_years} years')

    # Key skills — most important signal for the LLM
    key_skills = (user.get('key_skills') or '').strip()
    if key_skills:
        lines.append(f'Key skills & technologies: {key_skills}')

    # Custom instructions — user-defined AI behavior
    custom_instructions = (user.get('custom_instructions') or '').strip()
    if custom_instructions:
        lines.append(f'Custom instructions: {custom_instructions}')

    # Domain
    domain = (user.get('domain') or '').strip()
    if domain:
        lines.append(f'Domain/specialization: {domain}')

    if not lines:
        return ''

    lines.append(
        'IMPORTANT: Answer from the perspective of someone with the listed key skills and role. '
        'Only claim hands-on experience with technologies in the key skills or resume. '
        'For unlisted technologies say "I have studied" or "I am familiar with" — never claim direct experience.'
    )

    # When the interview role is a programming language, prevent domain-bleed
    # (e.g. a telecom engineer being interviewed for Python should NOT mention SIP/SS7)
    try:
        import config as _cfg
        _iview_role = getattr(_cfg, 'INTERVIEW_ROLE', 'general')
        if _iview_role in ('python', 'java', 'javascript', 'sql'):
            lines.append(
                f'CODING INTERVIEW FOCUS: This is a {_iview_role.capitalize()} coding interview. '
                'Keep all answers focused on programming concepts and the chosen language. '
                'Do NOT reference unrelated domain experience (telecom, SIP, SS7, production support, etc.) in coding answers.'
            )
    except Exception:
        pass

    return '\n'.join(lines)
