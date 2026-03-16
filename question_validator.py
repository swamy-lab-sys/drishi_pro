"""
Interview Question Validator

Accepts real interview questions, rejects:
- YouTube/tutorial audio
- Fillers, noise, hallucinations
- Platform commands
"""

import re
from typing import Tuple
from collections import Counter


# =============================================================================
# STT CORRECTION - Fix common Whisper misheard terms
# =============================================================================

STT_CORRECTIONS = {
    # CI/CD misheard variants
    r"\ba,?\s*c,?\s*d\b": "CI CD",
    r"\ba c d\b": "CI CD",
    r"\bca cd\b": "CI CD",
    r"\bc a c d\b": "CI CD",
    r"\bci cd\b": "CI/CD",
    r"\bci slash cd\b": "CI/CD",
    r"\bcontinuous integration continuous (delivery|deployment)\b": "CI/CD",
    r"\bsee\s*eye\s*see\s*dee\b": "CI/CD",
    r"\bintegration\s*continuous\b": "CI/CD",
    # SSH
    r"\bs s h\b": "SSH",
    r"\bss h\b": "SSH",
    # Django (many misheard variants)
    r"\bjungle\b": "Django",
    r"\bjango\b": "Django",
    r"\bdd\s*jango\b": "Django",
    r"\bd\s*django\b": "Django",
    r"\bddjango\b": "Django",
    # Python misheard
    r"\b4[\s-]*by[\s-]*thon\b": "Python",
    r"\bfour[\s-]*by[\s-]*thon\b": "Python",
    # Serializer
    r"\bserial address\b": "serializer",
    r"\bserial izer\b": "serializer",
    # Kubernetes
    r"\bcubernetes\b": "Kubernetes",
    r"\bcuber netties\b": "Kubernetes",
    r"\bk8s\b": "Kubernetes",
    # Other common mishearings
    r"\breston tuple\b": "list and tuple",
    r"\bpost grass\b": "PostgreSQL",
    r"\bpost gress\b": "PostgreSQL",
    r"\bred is\b": "Redis",
    r"\baws lamba\b": "AWS Lambda",
    # Palindrome
    r"\bball and rum\b": "Palindrome",
    r"\bpal and rom\b": "Palindrome",
    # Kubernetes terms
    r"\bconflict\s*map\b": "ConfigMap",
    r"\bconfig\s*map\b": "ConfigMap",
    r"\bsecrete?\b": "Secret",
    # Terraform
    r"\bterra\s*form\b": "Terraform",
    r"\bterraform applet\b": "Terraform apply",
    r"\bterra form apply\b": "Terraform apply",
    # Ansible
    r"\bansible\b": "Ansible",
    # Kafka
    r"\bcafka\b": "Kafka",
    r"\bkafka\b": "Kafka",
    # Grafana
    r"\bgrafana\b": "Grafana",
    r"\bgra fana\b": "Grafana",
    # kubectl
    r"\bkubectl locks\b": "kubectl logs",
    r"\bkubectl\b": "kubectl",
    # Django commands misheard
    r"\bmeet\s*migrations?\b": "makemigrations",
    r"\bmeat\s*migrations?\b": "makemigrations",
    r"\bmake\s*migrations?\b": "makemigrations",
    # *args/**kwargs misheard
    r"\barcs?\s*and\s*kw\s*arcs?\b": "*args and **kwargs",
    r"\barks?\s*and\s*kw\s*arks?\b": "*args and **kwargs",
    r"\barcs?\s*and\s*kwas?\b": "*args and **kwargs",
    r"\barks?\s*and\s*kwas?\b": "*args and **kwargs",
    # Generator misheard
    r"\bgenerate?\s*trip\b": "generator",
    r"\bgenerator\s*trip\b": "generator",
    # Microservices misheard
    r"\bmicro\s*letic\b": "microservices",
    r"\bmicro\s*litic\b": "microservices",
    # JWT misheard as GWT
    r"\bgwt\b": "JWT",
    r"\bg\s*w\s*t\b": "JWT",
    # Django ORM misheard
    r"\bdjango\s*over\s*m\b": "Django ORM",
    # CORS misheard
    r"\bcars\s*error": "CORS error",
    # Nginx misheard
    r"\bnvidia\s*architecture\s*engine\b": "Nginx",
    # "Write" misheard as "Right here/Right there/Righty" at sentence start
    r"^right\s+here,?\s*": "Write a ",
    r"^right\s+there,?\s*": "Write a ",
    r"^righty\s+": "Write an ",
    # Noise prefixes — background audio words before real question
    r"^async\s+out\s+there\.?\s*": "",
    r"^open\s*gl,?\s*": "",
    r"^ubuntu,?\s*": "",
    r"^graphql,?\s*": "",
    r"^websocket,?\s*": "",
    r"^graph,?\s*": "",
    # "Write an Ansible" misheard various ways (tiny.en / base.en)
    r"^variety\s+and\s+support\b": "Write an Ansible",
    r"^variety\s+and\b": "Write an Ansible",
    r"^break\s+a\s+ansible\b": "Write an Ansible",
    r"^break\s+an\s+ansible\b": "Write an Ansible",
    r"^break\s+a\b": "Write a",    # "Break a playbook/script/function"
    r"^break\s+an\b": "Write an",  # "Break an Ansible..."
    r"^redia,?\s+ansible\b": "Write an Ansible",  # "Redia, Ansible playable..." (tiny.en)
    r"^redia,?\s+": "Write a ",    # any "Redia ..." prefix
    # "playable" → "playbook" (tiny.en mishears)
    r"\bansible\s+playable\b": "Ansible playbook",
    r"\bplayable\s+for\b": "playbook for",
    # "Linux server" misheard
    r"\blinex\s+service\b": "Linux server",
    r"\blinex\s+server\b": "Linux server",
    r"\blinex\b": "Linux",
    # "What is S3" misheard as "Who stands in" by tiny.en
    r"\bwho\s+stands\s+in\s+aws\b": "What is S3 in AWS",
    r"\bwho\s+stands\b": "What is S3",
    # Django session misheard
    r"\bdjing\s+go\b": "Django",
    r"\bdj\s+go\b": "Django",
    # OOP misheard as OBS
    r"\bobs\s+concepts?\b": "OOP concepts",
    r"\bobs\s+principles?\b": "OOP principles",
    r"\bobs\s+concept\b": "OOP concept",
    # Write at start misheard as Righte
    r"^righte\b,?\.?\s*": "Write ",
    # Ansible Playbook misheard
    r"\band\s*so\s*won'?t\s*play\s*(both\s*)?": "Ansible Playbook ",
    r"\bansiblescript\b": "Ansible Playbook",
    # Autosys (job scheduler) misheard variants (Sarvam AI)
    r"\bautosis\b": "Autosys",
    r"\bauto\s+sis\b": "Autosys",
    r"\bauto\s+sys\b": "Autosys",
    # Linux 'rm' command misheard as 'Aaram' (Hindi word for rest)
    r"\baaram\s+command\b": "rm command",
    r"\baaram\b": "rm",
    # 'ps aux' misheard as 'PSM andi' (common Sarvam mishear)
    r"\bpsm\s+andi\b": "ps aux",
    r"\bps\s+andi\b": "ps aux",
    # cp/mv misheard
    r"\bsee\s*pee\s+and\s+em\s*vee\b": "cp and mv",
    r"\bsee\s*pee\b(?=\s+and|\s+command|\s+vs|\s+or)": "cp",
    # X-Wing → explain (start of sentence)
    r"^x-?wing\s+about\b": "explain about",
    r"^x-?wing\b": "explain",
    # Abstraction misheard as obstruction
    r"\bobstruction\b": "abstraction",
    r"\bobstraction\b": "abstraction",
    # Docker Swarm misheard as Docker spawn
    r"\bdocker\s+spawn\b": "Docker Swarm",
    # on-premises misheard
    r"\bamp\s+premieres\b": "on-premises",
    r"\bon\s+premieres\b": "on-premises",
    r"\bon\s+premise\b": "on-premises",
    r"\bon\s+prem\b": "on-premises",
    # auth token misheard as "art token"
    r"\bart,?\s+token\b": "auth token",
    # distil-small mishears "Write" at sentence start as "Right"/"Alright"/"Thank you"
    # Must be BEFORE ignore_pattern check so "Alright, a function to find..." isn't rejected
    r"^(alright|right),?\s*,?\s+a\s+(function|code|program|class|script|method|decorator|generator|closure)\b": r"Write a \2",
    r"^(alright|right),?\s*,?\s+an\s+(ansible|example|algorithm|iterator)\b": r"Write an \2",
    # "Right, , generator function" — STT drops "a", also catches "Right generator..."
    r"^(alright|right),?\s*,?\s+(generator|decorator|closure)\s+(function|class|example|pattern)\b": r"Write a \2 \3",
    r"^thank\s+you,?\s+a\s+(function|code|program|class|script|method|decorator|generator|closure)\b": r"Write a \1",
    r"^thank\s+you,?\s+an\s+(ansible|example|iterator)\b": r"Write an \1",
    # Also handle "Right function to find" (drops "a")
    r"^(alright|right),?\s*,?\s+(function|code|program|decorator|generator)\s+to\b": r"Write a \2 to",
    # Decorator misheard variants
    r"\bDecatur\b": "decorator",
    r"\bdecatur\b": "decorator",
    r"\bDecor\b(?=,|\s+in\b|\s+pattern|\s+function|\s+example)": "decorator",
    r"\bthe\s+creator\b": "the decorator",
    r"\ba\s+creator\b": "a decorator",
    r"\bcreator\b": "decorator",
    r"\bcurators\b": "decorators",
    # Generator misheard / word-order swapped
    r"\bGenrator\b": "generator",
    r"\bgenrator\b": "generator",
    r"\bgenerater\b": "generator",
    r"\bgenerated\s+function\b": "generator function",
    r"\bfunction\s+generated\b": "generator function",
    r"\bWrite\s+a\s+generated\s+function\b": "Write a generator function",
    r"\bwrite\s+a\s+generated\s+function\b": "Write a generator function",
    # Ansible new mishears — "A-Aunseboil" has hyphen so avoid \b word boundary
    r"a-aunseboil": "Ansible",   # must be before the bare aunseboil pattern
    r"\baunseboil\b": "Ansible",
    r"\baunseboil": "Ansible",   # catches "Aunseboil" at start of word
    # ── Strip Telugu/noise prefix words that appear before the real question ──
    r"^tammane?\s+": "",          # "Tammane How to deploy" → "How to deploy"
    r"^(yeh|ye|ek|ek baar|ab)\s+": "",   # Hindi filler at start
    # ── Deepgram-specific mishears (from live session logs) ──────────────────
    # "Write" → "Variety" at sentence start (Deepgram accent issue)
    r"^variety\s+an?\s+(ansible|decorator|generator|function|class|script|program)\b": r"Write an \1",
    r"^variety\s+(ansible|an\s+ansible)\b": "Write an Ansible",
    # "GIL" → "Gilmart" / "Gil mart"
    r"\bGilmart\b": "GIL",
    r"\bgil\s*mart\b": "GIL",
    # "polymorphism" → "polymer result" / "polymer mesh"
    r"\bpolymer\s+result\b": "polymorphism",
    r"\bpolymer\s+mesh\b": "polymorphism",
    r"\bpolymer\s+rhythm\b": "polymorphism",
    # "even numbers" → "email numbers" (acoustic similarity)
    r"\bemail\s+numbers?\b": "even numbers",
    r"\bemail\s+number\b": "even number",
    # "ConfigMap" → "configure" / "config map" in Kubernetes context
    r"\bconfigure\s+in\s+kubernetes\b": "ConfigMap in Kubernetes",
    r"\bconfigure\s+map\b": "ConfigMap",
    # "nginx" → "nine Linux" / "nine linux" / "engineering application in nine"
    r"\bnine\s+linux\b": "nginx on Linux",
    r"\bninex\b": "nginx",
    r"\bengineering\s+application\s+in\s+nine\b": "nginx",
    # "decorator" → "decorated impact"
    r"\bdecorated\s+impact\b": "decorator",
    # "palindrome" → "palin drum" (Deepgram variant)
    r"\bpalin\s+drum\b": "palindrome",
    r"\bpalindrome\b": "palindrome",
    # GIL misheard (gill with double-l is the most common Sarvam/Deepgram mishear)
    r"\bgill\b": "GIL",
    r"\bgills\b": "GIL",
    r"\bmy\s+grid\b": "GIL",
    r"\bgill\s+in\s+python\b": "GIL in Python",
    # manage.py misheard
    r"\bmanage\.pv\b": "manage.py",
    # Polymorphism misheard
    r"\bpolymonchism\b": "Polymorphism",
    r"\bpolymorfism\b": "Polymorphism",
    # YAML file misheard
    r"\byaml\s+finds?\b": "YAML file",
    # List comprehension misheard
    r"\blist\s+to\s+come\b": "list comprehension",
    # asyncio mishears (Sarvam/Deepgram variants — very common)
    r"\basinsio\b": "asyncio",
    r"\basin\s+sio\b": "asyncio",
    r"\basinsyo\b": "asyncio",
    r"\basinco\b": "asyncio",
    r"\basin\s+co\b": "asyncio",
    r"\basinchio\b": "asyncio",
    r"\baccensio\b": "asyncio",
    r"\basynchio\b": "asyncio",
    r"\baysinsio\b": "asyncio",
    r"\basin\s+show\b": "asyncio",
    # CI/CD no-space variant (Sarvam hears "cacd" as one word)
    r"\bcacd\b": "CI/CD",
    r"\bca\.cd\b": "CI/CD",
    r"\bkacd\b": "CI/CD",
    # Deep/shallow copy mishears (coffee sounds like copy to STT)
    r"\bdeep\s+coffee\b": "deep copy",
    r"\bdeep\s+cafe\b": "deep copy",
    r"\bshallow\s+coffee\b": "shallow copy",
    r"\bshallow\s+cafe\b": "shallow copy",
    r"\bdeepth\s+copy\b": "deep copy",
    # htop (space between h and top)
    r"\bh\s+top\b": "htop",
    r"\bhee\s+top\b": "htop",
    r"\beach\s+top\b": "htop",
    # autoscaling mishears — "auto seasoning" is the most common STT error for autoscaling
    r"\bauto\s+seasoning\b": "autoscaling",
    r"\bauto\s+seizing\b": "autoscaling",
    r"\bauto\s+sensing\b": "autoscaling",
    r"\bauto\s+sassing\b": "autoscaling",
    # threading / multiprocessing mishears
    r"\bmulti\s+processing\b": "multiprocessing",
    r"\bmulti\s+threading\b": "multithreading",
    r"\bsingle\s+thread\s+id\b": "single threaded",
    # ── SQL / Database mishears ───────────────────────────────────────────────
    # "SQL" commonly misheard as "sequel"
    r"\bsequel\s+query\b": "SQL query",
    r"\bsequel\s+server\b": "SQL Server",
    r"\bsequel\s+injection\b": "SQL injection",
    r"\bsequel\s+join\b": "SQL join",
    r"\bsequel\b(?=\s+(?:statement|command|syntax|database|table|index|view))": "SQL",
    # PostgreSQL mishears
    r"\bpost\s*grey\s*sql\b": "PostgreSQL",
    r"\bpost\s*gres\s*sql\b": "PostgreSQL",
    r"\bpost\s*gress\s*cue\s*el\b": "PostgreSQL",
    r"\bpostgres\s+ql\b": "PostgreSQL",
    # "ACID" misheard
    r"\bassid\s+(properties|transactions?|compliance)\b": "ACID \1",
    r"\bacid\s+compliance\b": "ACID compliance",
    # "MVCC" misheard
    r"\bm\s*v\s*c\s*c\b": "MVCC",
    r"\bmultiversion\s+concurrency\b": "MVCC",
    # "WAL" misheard
    r"\bwale\s+(log|logging|writer|file)\b": "WAL \1",
    r"\bwall\s+log\b": "WAL log",
    # "materialized view" misheard
    r"\bmaterial\s*ised\s*view\b": "materialized view",
    r"\bmaterialize\s*view\b": "materialized view",
    # "queryset" misheard
    r"\bquery\s*set\b": "queryset",
    # "JSONB" misheard
    r"\bjson\s*bee\b": "JSONB",
    r"\bjason\s*b\b": "JSONB",
    # Index misheard in DB context
    r"\bb\s*tree\s*index\b": "B-tree index",
    r"\bbtree\s*index\b": "B-tree index",
    # JOIN types misheard
    r"\bleft\s+outer\s+join\b": "LEFT JOIN",
    r"\bright\s+outer\s+join\b": "RIGHT JOIN",
    r"\bfull\s+outer\s+join\b": "FULL OUTER JOIN",
    # "CTE" / "common table expression"
    r"\bsee\s*tee\s*ee\b": "CTE",
    r"\bcommon\s+table\s+exp\b": "common table expression",

    # ── HTML / CSS mishears ───────────────────────────────────────────────────
    # "HTML" misheard
    r"\bh\s*t\s*m\s*l\b": "HTML",
    r"\bhaitch\s*t\s*m\s*l\b": "HTML",
    # "CSS" misheard
    r"\bc\s*s\s*s\b": "CSS",
    r"\bsee\s*s\s*s\b": "CSS",
    r"\bcascading\s+style\s+sheet\b": "CSS",
    # "flexbox" misheard
    r"\bflex\s+box\b": "flexbox",
    r"\bflex\s*box\b": "flexbox",
    # "z-index" misheard
    r"\bzed\s+index\b": "z-index",
    r"\bzee\s+index\b": "z-index",
    # "DOM" misheard
    r"\bdome\s+(manipulation|traversal|element|api|access)\b": r"DOM \1",
    r"\bDocument\s+Object\s+Model\b": "DOM",
    # "BEM" methodology
    r"\bb\s*e\s*m\s+(methodology|naming|css)\b": r"BEM \1",
    # "SASS" / "SCSS" misheard
    r"\bsass\s+(css|file|variable|mixin)\b": r"SASS \1",
    r"\bs\s*c\s*s\s*s\b": "SCSS",
    # "viewport" misheard
    r"\bview\s+port\b": "viewport",
    r"\bview\s*port\s+meta\b": "viewport meta",
    # "ARIA" misheard
    r"\baria\s+(label|role|attribute|accessibility)\b": r"ARIA \1",
    r"\barea\s+label\b": "ARIA label",
    # "SEO" misheard
    r"\bs\s*e\s*o\b": "SEO",

    # ── JavaScript mishears ───────────────────────────────────────────────────
    # "event loop" misheard
    r"\bevent\s+lupe?\b": "event loop",
    r"\bevent\s+lop\b": "event loop",
    # "prototype" misheard
    r"\bproto\s*type\s+(chain|inheritance)\b": r"prototype \1",
    r"\bproto\s+type\b": "prototype",
    # "hoisting" misheard
    r"\bhoisted?\s+(variable|function|declaration)\b": r"hoisted \1",
    r"\bhoising\b": "hoisting",
    r"\bhoising\b": "hoisting",
    # "closure" misheard
    r"\bclosure\s+(function|concept|scope|in\s+javascript)\b": r"closure \1",
    # "callback" misheard
    r"\bcall\s*back\s+(function|hell|pattern)\b": r"callback \1",
    # "promise" in JS context
    r"\bpromise\s+(chain|chaining|all|race|resolve|reject|then|catch)\b": r"promise \1",
    # "async/await" misheard
    r"\baysinc\b": "async",
    r"\baa\s*sink\b": "async",
    # "webpack" misheard
    r"\bweb\s*pack\b": "webpack",
    r"\bweb\s+pak\b": "webpack",
    # "babel" misheard
    r"\bbabble\s+(js|javascript|transpiler|config)\b": r"babel \1",
    # "querySelector" misheard
    r"\bquery\s+selector\b": "querySelector",
    r"\bquery\s+selector\s+all\b": "querySelectorAll",
    # "nullish coalescing" misheard
    r"\bnullish\s+coal\w+\b": "nullish coalescing",
    r"\bnull\s+coalescing\b": "nullish coalescing",
    # "optional chaining" misheard
    r"\boptional\s+chain\b": "optional chaining",
    # "destructuring" misheard
    r"\bdestruct\w+\s+(assignment|syntax)\b": r"destructuring \1",
    r"\bdestructing\b": "destructuring",
    # "SPA" = single page application
    r"\bsingle\s+page\s+app\w*\b": "SPA",
    r"\bs\s*p\s*a\b": "SPA",
    # "SSR" / "CSR"
    r"\bserver\s+side\s+render\w*\b": "SSR",
    r"\bclient\s+side\s+render\w*\b": "CSR",
    r"\bstatic\s+site\s+gen\w*\b": "SSG",
    # "virtual DOM" misheard
    r"\bvirtual\s+dome\b": "virtual DOM",
    r"\bviritual\s+dom\b": "virtual DOM",
    # "CORS" extended
    r"\bcross\s+origin\s+(resource\s+sharing|policy|issue|error)\b": r"CORS \1",
    # "NPM" / "yarn" misheard
    r"\bn\s*p\s*m\b": "npm",
    r"\bnode\s+package\s+manager\b": "npm",

    # ── Kubernetes mishears ───────────────────────────────────────────────────
    # "etcd" misheard
    r"\betc\s*d\b": "etcd",
    r"\bet\s*cd\b": "etcd",
    r"\betsy\s*d\b": "etcd",
    r"\be\s*t\s*c\s*d\b": "etcd",
    # "RBAC" misheard
    r"\br\s*b\s*a\s*c\b": "RBAC",
    r"\brole\s+based\s+access\s+control\b": "RBAC",
    # "PVC" / "PV" misheard
    r"\bpee\s*vee\s*see\b": "PVC",
    r"\bpersistent\s+volume\s+claim\b": "PVC",
    r"\bpersistent\s+volume\b": "PV",
    # "StatefulSet" misheard
    r"\bstateful\s+set\b": "StatefulSet",
    r"\bstate\s*full\s*set\b": "StatefulSet",
    r"\bstatefull\s*set\b": "StatefulSet",
    # "DaemonSet" misheard
    r"\bdemon\s*set\b": "DaemonSet",
    r"\bdaemon\s*set\b": "DaemonSet",
    r"\bdemon\s+sets?\b": "DaemonSet",
    # "CronJob" misheard
    r"\bcron\s+job\b": "CronJob",
    r"\bcron\s*job\b": "CronJob",
    # "CRD" misheard
    r"\bc\s*r\s*d\b": "CRD",
    r"\bcustom\s+resource\s+definition\b": "CRD",
    # "HPA" misheard
    r"\bh\s*p\s*a\b": "HPA",
    r"\bhorizontal\s+pod\s+auto\s*scal\w+\b": "HPA",
    # "VPA" misheard
    r"\bv\s*p\s*a\b": "VPA",
    r"\bvertical\s+pod\s+auto\s*scal\w+\b": "VPA",
    # "liveness probe" / "readiness probe" misheard
    r"\blive\s*ness\s+probe\b": "liveness probe",
    r"\bread\s*iness\s+probe\b": "readiness probe",
    r"\bstart\s*up\s+probe\b": "startup probe",
    # "Helm chart" misheard
    r"\bhelm\s*chart\b": "Helm chart",
    r"\bhelm\s+chars?\b": "Helm chart",
    # "kubeconfig" misheard
    r"\bkube\s*config\b": "kubeconfig",
    r"\bkube\s+config\b": "kubeconfig",
    # "CNI" misheard
    r"\bc\s*n\s*i\s+(plugin|interface|network)\b": r"CNI \1",
    r"\bcontainer\s+network\s+interface\b": "CNI",
    # "Pod" concepts
    r"\bpod\s+affin\w+\b": "pod affinity",
    r"\bnode\s+affin\w+\b": "node affinity",
    r"\btaint\s+tolerat\w+\b": "taints and tolerations",
    r"\btolerations?\s+taint\b": "taints and tolerations",

    # ── OpenStack mishears ────────────────────────────────────────────────────
    r"\bhorizon\s+(dashboard|ui|panel)\b": r"Horizon \1",
    r"\boctavia\s+(load\s*balancer|lb)\b": r"Octavia \1",
    r"\bbarbican\s+(secrets?|key|certificate)\b": r"Barbican \1",
    r"\bironic\s+(bare\s*metal|node|service)\b": r"Ironic \1",
    r"\bfloating\s+i\s*p\b": "floating IP",
    r"\bsecurity\s+group\b": "security group",
    r"\bml\s*2\s*plugin\b": "ML2 plugin",
    r"\bvx\s*lan\b": "VXLAN",
    r"\bl\s*3\s*agent\b": "L3 agent",
    r"\bdhcp\s*agent\b": "DHCP agent",
    r"\blive\s*migrat\w+\b": "live migration",
    r"\bcold\s*migrat\w+\b": "cold migration",

    # ── Java mishears ─────────────────────────────────────────────────────────
    r"\bgarbage\s+collect\w+\b": "garbage collection",
    r"\bgg\s*c\b(?=\s)": "GC",
    r"\bg\s*1\s+garbage\b": "G1 GC",
    r"\bjit\s+compil\w+\b": "JIT compiler",
    r"\bjust\s+in\s+time\s+compil\w+\b": "JIT compiler",
    r"\bclass\s*loader\b": "classloader",
    r"\bthread\s*pool\b": "thread pool",
    r"\bexecutor\s*service\b": "ExecutorService",
    r"\bcompletable\s*future\b": "CompletableFuture",
    r"\barray\s*list\b": "ArrayList",
    r"\blinked\s*list\b": "LinkedList",
    r"\bhash\s*map\b": "HashMap",
    r"\btree\s*map\b": "TreeMap",
    r"\bhash\s*set\b": "HashSet",
    r"\bconcurrent\s*hash\s*map\b": "ConcurrentHashMap",
    r"\bstring\s*builder\b": "StringBuilder",
    r"\bstring\s*buffer\b": "StringBuffer",
    r"\bauto\s*boxing\b": "autoboxing",
    r"\btype\s+erasure\b": "type erasure",
    r"\bfunctional\s+interface\b": "functional interface",
    r"\bstream\s+api\b": "Stream API",
    r"\bspring\s+boot\b": "Spring Boot",
    r"\bspring\s+m\s*v\s*c\b": "Spring MVC",
    r"\bspring\s+security\b": "Spring Security",
    r"\bhikari\s*cp\b": "HikariCP",
    r"\bhibernate\s+(session|query|mapping|orm)\b": r"Hibernate \1",
    r"\bj\s*p\s*a\b(?=\s)": "JPA",
    r"\bj\s*d\s*b\s*c\b": "JDBC",
    r"\bsolid\s+(principle|design|concept)\b": r"SOLID \1",
    r"\bsingleton\s+(pattern|design)\b": r"Singleton \1",
    r"\bfactory\s+(pattern|design|method)\b": r"Factory \1",
    r"\bbuilder\s+(pattern|design)\b": r"Builder \1",
    r"\bobserver\s+(pattern|design)\b": r"Observer \1",

    # ── Django/Flask mishears ─────────────────────────────────────────────────
    r"\bdjango\s+quer\s*y\s*set\b": "Django queryset",
    r"\bquery\s*set\s+(api|filter|method|lazy)\b": r"queryset \1",
    r"\bselect\s+related\b": "select related",
    r"\bprefetch\s+related\b": "prefetch related",
    r"\bn\s*plus\s*1\s+(problem|issue|query)\b": r"N+1 \1",
    r"\bmanage\s+dot\s+py\b": "manage.py",
    r"\bmanage\s+point\s+py\b": "manage.py",
    r"\bmodel\s*admin\b": "ModelAdmin",
    r"\bmodel\s*form\b": "ModelForm",
    r"\bclass\s+based\s+view\b": "class-based view",
    r"\bfunction\s+based\s+view\b": "function-based view",
    r"\bdjango\s+rest\s+framework\b": "DRF",
    r"\bview\s*set\b": "ViewSet",
    r"\bmodel\s*view\s*set\b": "ModelViewSet",
    r"\bgeneric\s+api\s+view\b": "GenericAPIView",
    r"\bdjango\s+signal\b": "Django signal",
    r"\bpost\s+save\s+signal\b": "post save signal",
    r"\bpre\s+save\s+signal\b": "pre save signal",
    r"\bcontext\s*processor\b": "context processor",
    r"\btemplate\s+tag\b": "template tag",
    r"\btemplate\s+filter\b": "template filter",
    r"\bdjango\s+celery\b": "Django Celery",
    r"\bflask\s+blueprint\b": "Flask Blueprint",
    r"\bjinja\s*2\b": "Jinja2",
    r"\bjinja\s+(template|syntax|filter|macro)\b": r"Jinja2 \1",
    r"\bwizzy\b": "WSGI",
    r"\bwhisky\s+(server|interface)\b": "WSGI \1",
    r"\bw\s*s\s*g\s*i\b": "WSGI",
    r"\bgunic\w+\b": "gunicorn",
    r"\bguni\s*corn\b": "gunicorn",
    r"\bgreen\s+unicorn\b": "gunicorn",
    r"\bu\s*w\s*s\s*g\s*i\b": "uWSGI",
    r"\bflask\s+sql\s*alchemy\b": "Flask-SQLAlchemy",
    r"\bflask\s+wtf\b": "Flask-WTF",

    # ── SRE / Observability mishears ──────────────────────────────────────────
    r"\berror\s+budg\w+\b": "error budget",
    r"\bslo\s+(target|violation|window)\b": r"SLO \1",
    r"\bsli\s+(metric|measurement)\b": r"SLI \1",
    r"\bgolden\s+signals?\b": "golden signals",
    r"\bopen\s*telemetry\b": "OpenTelemetry",
    r"\botel\s+(sdk|collector|trace|metric)\b": r"OpenTelemetry \1",
    r"\bjaeger\s+(trace|tracing)\b": r"Jaeger \1",
    r"\bzip\s*kin\b": "Zipkin",
    r"\belastic\s*search\b": "Elasticsearch",
    r"\belog\s*stash\b": "Logstash",
    r"\belk\s+(stack|setup)\b": r"ELK \1",
    r"\bsplunk\s+(query|search|index)\b": r"Splunk \1",
    r"\bdatadog\s+(agent|apm|metric)\b": r"Datadog \1",
    r"\balert\s*manager\b": "Alertmanager",
    r"\bburn\s+rate\s+(alert|slo)\b": r"burn rate \1",
    r"\bdistributed\s+trac\w+\b": "distributed tracing",
    r"\bcorrelation\s+id\b": "correlation ID",
    r"\btrace\s+id\b": "trace ID",

    # ── DevOps tools mishears ─────────────────────────────────────────────────
    r"\bsonar\s*qube\b": "SonarQube",
    r"\bsonar\s+(scan|analysis|report)\b": r"SonarQube \1",
    r"\bnexus\s+(repo|registry|artifact)\b": r"Nexus \1",
    r"\bartifact\s*ory\b": "Artifactory",
    r"\bgithub\s+action\b": "GitHub Actions",
    r"\bgitlab\s+(ci|pipeline|runner)\b": r"GitLab CI \1",
    r"\bcircle\s*ci\b": "CircleCI",
    r"\bargo\s*cd\b": "ArgoCD",
    r"\bflux\s*cd\b": "FluxCD",
    r"\bsecret\s+management\b": "secret management",
    r"\bhashi\s*corp\s*vault\b": "HashiCorp Vault",
    r"\bvault\s+(token|secret|policy|approle)\b": r"Vault \1",
    r"\bgit\s*ops\b": "GitOps",
    r"\bblue\s+green\s+(deploy\w*|release)\b": r"blue-green \1",
    r"\bcanary\s+(deploy\w*|release|testing)\b": r"canary \1",
    r"\bfeature\s+flag\b": "feature flag",
    r"\bfeature\s+toggle\b": "feature toggle",
    r"\bchaos\s+engineer\w+\b": "chaos engineering",
    r"\bk\s*6\s+(test|load)\b": r"k6 \1",
    r"\bjmeter\b": "JMeter",
    r"\blocust\s+(test|script)\b": r"Locust \1",

    # Python keyword STT mishears
    r"\bnon[\s-]local\b": "nonlocal",
    r"\bnon[\s-]locals\b": "nonlocals",
    r"\btry\s+and\s+accept\b": "try and except",
    r"\baccept\s+(keyword|block|clause|statement|handler)\b": r"except \1",
    r"\bdelky\s+word\b": "del keyword",
    r"\bdelky\b": "del",
    r"\bdel\s*ky\b": "del",
    # Lambda mishears
    r"\blamda\b": "lambda",
    r"\blambda\s+function\b": "lambda function",
    # context manager mishears
    r"\bcontact\s+manager\b": "context manager",
    r"\bcontent\s+manager\b": "context manager",
    # metaclass mishears
    r"\bmeta\s+class\b": "metaclass",
    r"\bmeta\s+classes\b": "metaclasses",
    # Pickling
    r"\btickl(ing|e)\b": "pickling",
    # CAP theorem
    r"\bboot\s+and\s+cap\s+situation\b": "CAP theorem",
    r"\bcap\s+situation\b": "CAP theorem",
    # "What a signal" → question form
    r"^what\s+a\s+signal\.?$": "What is Django signal?",
    # async misheard as "essence"
    r"^what\s+is\s+essence\??$": "What is async?",
    # Method overriding misheard
    r"\bover\s*guiding\b": "overriding",
    r"\bover\s*riding\b": "overriding",
    # Palindrome misheard variants
    r"\bfallen\s*drum\b": "palindrome",
    r"\bfall\s*in\s*drum\b": "palindrome",
    r"\bpal\s*in\s*drum\b": "palindrome",
    # Fibonacci misheard variants
    r"\bmochi\s*series\b": "Fibonacci series",
    r"\bmocha\s*series\b": "Fibonacci series",
    r"\bfibonocci\b": "fibonacci",
    r"\bkibonocchi\b": "fibonacci",
    r"\bkibonochi\b": "fibonacci",
    r"\bfibo\s*nacci\b": "fibonacci",
    # Pickling misheard
    r"\bprickling\b": "pickling",
    r"\bun.prickling\b": "unpickling",
    r"\bun.pickling\b": "unpickling",
    # Alwrite (Whisper mishears "alright write" as "alwrite")
    r"^alwrite\s+": "Write ",
    r"^alright,?\s+write\s+": "Write ",
    # Typos / STT misheard
    r"\benencapsulation\b": "encapsulation",
    r"\bpolymerfism\b": "polymorphism",
    r"\bpolymerphism\b": "polymorphism",
    r"\bpolymorphysm\b": "polymorphism",
    r"\bhashmap\b": "hash map",
    r"\bdeepth\b": "depth",
    r"\bstructued\b": "structured",
    r"\bscripted\b": "Scripted",
    r"\bdeclarative\b": "Declarative",
    # "explain" misheard as "ask my"
    r"\bask\s+my\b": "explain",

    # ── Bash special variables — STT spells them out as words ─────────────────
    r"\bdollar\s+hash\b": "$#",                    # number of arguments
    r"\bdollar\s+question\s+mark\b": "$?",          # last exit status
    r"\bdollar\s+star\b": "$*",                    # all positional params
    r"\bdollar\s+at\s+sign\b": "$@",               # all params as array
    r"\bdollar\s+at\b": "$@",
    r"\bdollar\s+zero\b": "$0",                    # script name
    r"\bdollar\s+one\b": "$1",                     # first argument
    r"\bdollar\s+two\b": "$2",                     # second argument
    r"\bdollar\s+exclamation\s+mark\b": "$!",      # last background PID
    r"\bdollar\s+exclamation\b": "$!",
    r"\bdollar\s+dash\b": "$-",                    # current shell options
    r"\bdollar\s+underscore\b": "$_",              # last arg of previous command
    r"\bdollar\s+dollar\b": "$$",                  # current shell PID
    r"\bdollar\s+ampersand\b": "$&",               # matched string (sed)
    r"\bdollar\s+at\s+the\s+rate\b": "$@",
    # Also handle "hash" alone in context like "what does $# mean"
    r"\$\s+hash\b": "$#",
    r"\$\s+question\s+mark\b": "$?",

    # ── Unix/Shell command STT mishears ───────────────────────────────────────
    r"\bsed\s+command\b": "sed command",
    r"\bawk\s+command\b": "awk command",
    r"\bx\s+args\b": "xargs",
    r"\bx\s*arg\s+command\b": "xargs",
    r"\bnamed\s+pipe\b": "named pipe",
    r"\bfifow\b": "FIFO",
    r"\bhurd\s+link\b": "hard link",
    r"\bhard\s+link\s+vs\s+soft\b": "hard link vs soft link",
    r"\bsym\s+link\b": "symlink",
    r"\bsim\s+link\b": "symlink",
    r"\bshib\s+bang\b": "shebang",
    r"\bshee\s+bang\b": "shebang",
    r"\bshe\s+bang\b": "shebang",
    r"\bsha\s+bang\b": "shebang",
    r"\bcron\s+tab\b": "crontab",
    r"\bcron\s+expression\b": "cron expression",
    r"\bpipe\s+line\b": "pipeline",
    r"\bstd\s+in\b": "stdin",
    r"\bstd\s+out\b": "stdout",
    r"\bstd\s+err\b": "stderr",
    r"\bed\s+i\s+t\s+in\s+place\b": "edit in place",
    r"\bin\s+place\s+edit\b": "in-place edit",
    r"\bprocess\s+substitution\b": "process substitution",
    r"\bcommand\s+substitution\b": "command substitution",
    r"\bhere\s+doc\b": "heredoc",
    r"\bhere\s+document\b": "heredoc",
    r"\bhere\s+string\b": "here string",
    r"\bback\s+tick\b": "backtick",
    r"\bback\s+ticks\b": "backticks",
    r"\bsub\s+shell\b": "subshell",
    r"\bsub\s+process\b": "subprocess",
    r"\bset\s+e\s+flag\b": "set -e flag",
    r"\bset\s+minus\s+e\b": "set -e",
    r"\bset\s+minus\s+x\b": "set -x",
    r"\bset\s+minus\s+u\b": "set -u",
    r"\bset\s+minus\s+o\b": "set -o",
    r"\bset\s+minus\s+pipe\s+fail\b": "set -o pipefail",
    r"\bpipe\s+fail\b": "pipefail",
    r"\berr\s+exit\b": "errexit",
    r"\bnoun\s+set\b": "nounset",
    r"\bfile\s+descriptor\b": "file descriptor",
    r"\bfile\s+descriptors\b": "file descriptors",
    r"\bopen\s+file\s+descriptor\b": "open file descriptor",
    r"\bregular\s+expression\b": "regular expression",
    r"\bregex\s+in\s+bash\b": "regex in bash",
    r"\bgrep\s+pattern\b": "grep pattern",
    r"\bgrep\s+recursive\b": "grep -r",
    r"\bgrep\s+inverse\b": "grep -v",
    r"\bgrep\s+ignore\s+case\b": "grep -i",
    r"\bawk\s+field\s+separator\b": "awk field separator",
    r"\bawk\s+begin\s+end\b": "awk BEGIN END",
    r"\bsed\s+in\s+place\b": "sed -i",
    r"\bsed\s+substitute\b": "sed s command",
    r"\btrap\s+signal\b": "trap signal",
    r"\bsignal\s+handler\b": "signal handler",
    r"\bjob\s+control\b": "job control",
    r"\bfore\s+ground\b": "foreground",
    r"\bback\s+ground\b": "background",
    r"\bumask\s+value\b": "umask value",
    r"\bumask\s+command\b": "umask command",
    r"\boc\s+tal\b": "octal",
    r"\boc\s*tal\s+permission\b": "octal permission",
    r"\bchmod\s+seven\s+seven\s+seven\b": "chmod 777",
    r"\bchmod\s+seven\s+five\s+five\b": "chmod 755",
    r"\bchmod\s+six\s+four\s+four\b": "chmod 644",
    r"\bposix\s+standard\b": "POSIX standard",
    r"\bposic\b": "POSIX",
    r"\bposix\b": "POSIX",
    # OpenStack terms misheard
    r"\bopen\s*sit\b": "OpenShift",
    r"\bopen[\s-]*set\b": "OpenShift",
    r"\bopen\s*savio\b": "OpenStack",
    r"\bopen\s*sav\w+\b": "OpenStack",
    r"\bnawakama\b": "nova.conf",
    r"\bnf[\s_-]?com[\s_-]?track[\s_-]?mo\w*\b": "nf_conntrack",
    r"\bnf\s*com\s*track\b": "nf_conntrack",
    r"\bobvious\s+system\b": "OVS",
    r"\bself[\s-]state\b": "ERROR state",
    r"\bopen\s*stack\b": "OpenStack",
    r"\bopen\s*shift\b": "OpenShift",
    # Linux patching misheard
    r"\bdf[\s-]fn\w+\b": "df -h",
    r"\bdfa[\s-]f\w+\b": "df -h",
    # KVM/QEMU misheard
    r"\bkevm\b": "KVM",
    r"\bkolla\s+ansible\b": "Kolla-Ansible",
    # "X for example" / "example of X" → canonical "Give an example of X"
    r"^(\w[\w\s]+?)\s+for\s+example\s*\.?$": r"Give an example of \1",
    r"^(\w[\w\s]+?),?\s+give\s+(me\s+)?an?\s+example\s*\.?$": r"Give an example of \1",
    r"^example\s+of\s+(\w[\w\s]+?)\s*\.?$": r"Give an example of \1",
    r"^write\s+example\s+for\s+(\w[\w\s]+?)\s*\.?$": r"Write an example of \1",
}

COMPILED_STT_CORRECTIONS = [(re.compile(p, re.IGNORECASE), r) for p, r in STT_CORRECTIONS.items()]



def apply_stt_corrections(text: str) -> str:
    """Fix common Whisper misheard technical terms."""
    for pattern, replacement in COMPILED_STT_CORRECTIONS:
        text = pattern.sub(replacement, text)
    return text


# =============================================================================
# VAGUE QUESTION DETECTION - Reject pronoun-only follow-ups
# =============================================================================

VAGUE_PRONOUNS = {"it", "this", "that", "them", "they", "those", "these", "its"}

def is_vague_question(text: str) -> bool:
    """Reject vague follow-up questions with only pronouns, no specific subject.

    Examples rejected:
    - "How do you implement it?"
    - "Can you explain that?"
    - "What does it do?"

    Examples allowed:
    - "How do you implement CI/CD?" (has tech term)
    - "What is Docker?" (has tech term)
    """
    lower = text.lower().strip().rstrip("?.,!")
    words = lower.split()

    if len(words) < 3 or len(words) > 8:
        return False

    # Check if any real tech term exists
    has_tech = _has_tech_term(lower)
    if has_tech:
        return False

    # Check if the only "subject" words are pronouns
    filler_words = {"how", "do", "does", "did", "you", "we", "can", "could", "would",
                    "should", "will", "what", "is", "are", "was", "were", "a", "an",
                    "the", "to", "for", "in", "on", "about", "explain", "describe",
                    "tell", "me", "implement", "use", "work", "mean", "define"}

    subject_words = [w for w in words if w not in filler_words and w not in VAGUE_PRONOUNS]

    # If no subject words AND has a vague pronoun -> reject
    if not subject_words and any(w in VAGUE_PRONOUNS for w in words):
        return True

    return False


# =============================================================================
# YOUTUBE / TUTORIAL DETECTION - Reject non-interview audio
# =============================================================================

YOUTUBE_PATTERNS = [
    # Conversational noise / side-talk that leaks through STT
    r"i('ll| will) be (here|back|right) (in a|with you|in one)",
    r"i('m| am) (also|here|looking|checking)",
    r"(that's|this is) (a source|also a source)",
    r"(sql|unix) (is supported|not supported|is asked)",
    r"i just asked (a few|some)",
    r"(hyphen|minus) [a-z][,.]?\s+(that'?s? what was given|that is what)",
    # Translated side-conversation noise (Telugu/Tamil/Hindi STT artifacts)
    r"i('ll| will) have a word with",
    r"(miss|sir|anna|bro|brother|ma'?am)[.!,]?\s*$",  # ends with just a name/title
    r"^(hello|hi|hey)[.,]?\s*(miss|sir|anna|bro|ma'?am)",
    r"i('ll| will) (speak|talk|chat) with (you|her|him|them)",
    r"(please |kindly )?(hold on|wait|one moment|just a moment|just a second|one second|bear with)",
    # TV / screen-sharing noise from translated side conversations
    r"^(did you |do you |can you )?(see|show|watch|put).{0,20}(on the |to the |in the )?(tv|screen|monitor|display)",
    r"^do you have (a |the )?tv\b",
    r"^(put|show) it on (the )?(tv|screen)",
    r"^(see|watch) it on (the )?(tv|screen)",
    # Generic non-technical filler questions from translation artifacts
    r"^(momma|mama|mummy|mom|dad|bhai|anna|akka|sir)[,.]",
    r"^what do you do for\??\s*$",       # too vague — no subject
    r"^how do you do troubleshoot\??\s*$",  # incomplete question without context
    r"^did (you|they) see (this|it|that)",
    r"^(ready|done|okay|ok)\s*\??\s*$",   # pure filler
    # Candidate self-assessments / side-talk (not questions)
    r"^(no|i have no|i don't have|i don't)\s+(experience|exp|knowledge)\s*(in|with|on|of|about)?\s*(this|that|it)?\s*\.?\s*$",
    r"^not\s+(sure|experienced?|aware)\s*(about|in|with|of)?\s*(this|that|it)?\s*\.?\s*$",
    r"^(i'm?|i am)\s+not\s+(sure|experienced?|aware|familiar)\b",
    # Meta / tool questions not related to the interview topic
    r"(use|using|just use)\s+chat\s*gpt",
    r"(why|what).{0,30}(attend|use|need).{0,20}(your|this)\s+(work|tool|app|software)",
    r"why do i (have to|need to) (attend|use)",
    r"subscribe", r"like and subscribe", r"hit the bell",
    r"in this video", r"in today's video", r"in this tutorial",
    r"welcome to (my|this|the) (channel|video|tutorial|course|series)",
    r"hey (guys|everyone|everybody)", r"what's up (guys|everyone)",
    r"hello (everyone|guys|friends)", r"hi (guys|everyone)",
    r"let's (get started|begin|dive|jump|look)", r"let me show you",
    r"as you can see", r"on (the|your) screen",
    r"first (we need to|let's|we will|we'll)", r"step (one|two|three|1|2|3)",
    r"(next|now) (we|let's|I'll|I will|we'll)", r"moving on to",
    r"(click|go to|navigate|open) (on|the|this|here)",
    r"(link|links) (in|is in) (the|my) description",
    r"(leave|drop) a comment", r"comment (below|down)",
    r"share this video", r"don't forget to",
    r"thanks for watching", r"see you (in the|next)",
    r"(if you|you should) (liked|enjoyed|found)", r"please (like|share|subscribe)",
    r"(sponsored|brought to you) by",
    r"(check out|visit) (my|our|the) (website|patreon|github|link)",
    r"before we (start|begin|continue|proceed)",
    r"(so|okay|alright|now),?\s+(let's|I'll|we'll|let me)\s+(start|begin|install|setup|configure|learn|see|look|run|open|create|build|go|do|move|jump|proceed|continue|check)",
    r"(chapter|section|part) (one|two|three|\d+)",
    r"(prerequisite|before you|you need to) (know|have|install|understand)",
    r"(watch|see) (my|the) (previous|last|earlier|other) video",
    r"(i'll|i will|we'll|we will) (explain|show|demonstrate|walk you through)",
    r"(follow along|code along|type along)",
    r"(here|this) is (the|a|my|our) (output|result|demo|example)",
    r"(pause|stop) (the|this) video",
    r"(python|programming|coding) (tutorial|course|lesson|series|bootcamp)",
    r"(beginner|intermediate|advanced) (guide|tutorial|course)",
    r"(learn|learning|master|mastering) (python|programming|coding|django)",
]

COMPILED_YOUTUBE = [re.compile(p, re.IGNORECASE) for p in YOUTUBE_PATTERNS]


def is_youtube_or_tutorial(text: str) -> bool:
    """Detect YouTube/tutorial audio content (not interview questions)."""
    if not text or len(text) < 10:
        return False

    lower = text.lower()

    for pattern in COMPILED_YOUTUBE:
        if pattern.search(lower):
            return True

    words = lower.split()
    if len(words) > 40:
        return True

    if len(words) > 20:
        tutorial_words = {'video', 'tutorial', 'channel', 'subscribe', 'course',
                          'lesson', 'click', 'link', 'website', 'download',
                          'install', 'setup', 'screen', 'demo', 'example',
                          'output', 'result', 'step', 'chapter', 'section'}
        found = sum(1 for w in words if w in tutorial_words)
        if found >= 3:
            return True

    return False


# =============================================================================
# QUESTION STARTERS
# =============================================================================

QUESTION_STARTERS = [
    "what is", "what are", "what does", "what do", "what's",
    "why is", "why do", "why does", "why would",
    "how do", "how does", "how to", "how can", "how would",
    "when do", "when does", "when should", "when would",
    "where do", "where does", "where is",
    "which", "is there", "are there", "can you", "could you",
    "explain", "describe", "define", "compare", "tell me",
    "difference between", "walk me through",
    "write", "implement", "create", "give me",
    "have you", "do you have", "how much", "how many years",
    # Command/task starters common in Linux/production interviews
    "show", "show me", "show the", "show how",
    "display", "display the",
    "print", "print the",
    "find", "find all", "find the",
    "list", "list all", "list the",
    "match", "match all",
    "search", "search for",
    "check", "check the", "check if",
    "count", "count the",
    "filter", "filter the",
    "sort", "sort the",
    # Unix/shell task starters
    "replace", "replace the", "replace all",
    "rename", "move", "copy",
    "redirect", "pipe",
    "extract", "extract the",
    "delete", "delete the",
    "kill", "stop", "restart",
    "monitor", "monitor the",
    "schedule",
    "compress", "archive",
    "mount", "unmount",
    "configure", "set up",
    "debug", "trace",
]

TECH_TERMS = {
    "python", "class", "function", "method", "decorator", "decorators", "generator",
    "gil", "global interpreter lock",
    "list", "tuple", "dict", "dictionary", "set", "string", "array",
    "inheritance", "polymorphism", "encapsulation", "abstraction",
    "django", "flask", "api", "rest", "database", "sql", "orm",
    "docker", "kubernetes", "aws", "git", "ci/cd", "pipeline",
    "asyncio", "async", "await", "thread", "process", "memory", "garbage",
    "autoscaling", "auto scaling", "deep copy", "shallow copy",
    "multithreading", "multiprocessing", "lambda function", "context manager",
    "metaclass", "metaclasses",
    "exception", "error", "try", "except", "loop", "recursion",
    "lambda", "closure", "scope", "variable", "module", "package",
    "import", "virtual", "environment", "pip", "pytest", "unittest",
    "serializer", "middleware", "authentication", "authorization",
    "cache", "redis", "celery", "microservice", "monolith",
    "deployment", "container", "pod", "helm", "terraform",
    "branch", "merge", "commit", "pull request", "cicd",
    "agile", "scrum", "sprint", "devops", "cloud",
    # Programming languages — must be recognized to avoid rejection of "What is Java?"
    "java", "javascript", "typescript", "golang", "rust", "kotlin", "swift",
    "ruby", "php", "scala", "perl", "c++", "c#", "r language",
    "node", "nodejs", "react", "angular", "vue", "express",
    "spring", "springboot", "spring boot", "hibernate", "jvm", "jdk", "jre",
    "maven", "gradle", "junit", "struts", "servlet", "jsp",
    "nextjs", "nuxt", "svelte", "graphql", "grpc",
    # Common interview topic words that are clearly IT
    "identifier", "mutable", "immutable", "operator", "operand",
    "compile", "interpreter", "runtime", "syntax", "semantics",
    "pointer", "reference", "stack", "heap", "queue", "linked list",
    "binary tree", "hash map", "complexity", "big o",
    "constructor", "destructor", "interface", "abstract",
    "overloading", "overriding", "polymorphism",
    "concurrency", "parallelism", "deadlock", "mutex", "semaphore",
    "regex", "regular expression", "json", "xml", "yaml",
    "token", "lexer", "parser", "compiler",
    # ITSM / Support frameworks
    "itil", "itsm", "sla", "slo", "sli", "incident", "change management",
    "problem management", "service desk", "cmdb", "runbook", "escalation",
    "mttr", "mttd", "mttf", "rca", "root cause", "post-mortem", "postmortem",
    "on-call", "oncall", "pagerduty", "opsgenie", "jira", "servicenow",
    # Linux/production support terms
    "memory leak", "disk usage", "cpu usage", "load average", "swap",
    "inode", "process", "daemon", "socket", "port", "firewall", "iptables",
    "systemctl", "journalctl", "cron", "crontab", "log rotation", "logrotate",
    "df", "du", "top", "htop", "vmstat", "iostat", "sar", "netstat", "ss",
    "pgrep", "pkill", "strace", "lsof", "tcpdump", "nmap", "ssh", "scp",
    "rsync", "grep", "awk", "sed", "find", "chmod", "chown", "tar", "gzip",
    # Basic Linux commands often asked in interviews — must be tech-recognized
    "pid", "pgid", "ppid",           # process IDs
    "mv", "cp", "rm", "ls", "ps",   # basic commands
    "pwd", "cat", "echo", "touch",  # basic commands
    "mkdir", "rmdir", "kill", "killall",  # more commands
    "wc", "sort", "uniq", "cut", "tee",   # text processing
    "env", "export", "alias", "history",  # shell builtins
    "autosys",  # enterprise job scheduler
    # DevOps/SRE terms
    "prometheus", "grafana", "monitoring", "metrics", "alerting",
    "kafka", "zookeeper", "broker", "topic", "partition",
    "jenkins", "ansible", "terraform", "argo", "argocd", "playbook",
    "configmap", "secret", "namespace", "ingress", "service",
    "kubectl", "eks", "ecs", "ec2", "s3", "iam", "vpc",
    "cloudwatch", "cloudfront", "load balancer", "autoscaling",
    "infrastructure", "provisioning", "automation",
    "nginx", "apache", "reverse proxy", "ssl", "tls",
    "linux", "bash", "shell", "script", "cron",
    "openshift", "rancher", "istio", "envoy",
    # OpenStack services
    "openstack", "nova", "neutron", "cinder", "glance", "keystone", "swift", "heat", "kolla",
    "nova-compute", "nova-api", "nova-conductor", "nova-scheduler",
    "ovs", "openvswitch", "open vswitch", "ovs-vsctl",
    "kvm", "qemu", "libvirt", "hypervisor", "ceph", "lvm",
    "migration", "live migration", "cold migration", "evacuate",
    # Linux/SRE commands and concepts
    "nf_conntrack", "conntrack", "iptables", "netfilter", "firewall",
    "lsof", "iostat", "vmstat", "iotop", "htop", "lsblk", "fdisk", "fstab",
    "fsck", "grub", "selinux", "sestatus", "inode",
    "load average", "iowait", "cpu utilization",
    "patching", "yum", "dnf", "apt",
    "read-only", "mount point", "file system", "remount",
    "single user mode", "rescue mode", "recovery mode",
    "open files", "file descriptor",
    "disk io", "block device", "storage",
    # Networking
    "tcp", "udp", "dns", "dhcp", "nat", "vlan", "vxlan",
    "bridge", "veth", "tap interface",
    # NFS/SAN/NAS
    "nfs", "san", "nas", "cifs",
    "rollout", "rollback", "canary", "blue green",
    "log", "logging", "tracing", "observability",
    "annotation", "label", "selector", "replica",
    "node", "cluster", "scaling", "hpa",
    "yaml", "json", "xml", "config",
    "module", "provider", "state", "plan", "apply",
    "troubleshoot", "debug", "performance", "optimize",
    "production", "staging", "deployment", "outage", "downtime",
    "production issue", "production support", "production problem",
    "handle", "incident response", "escalation", "on call",
    "experience", "responsibility", "profile", "tool",
    "component", "configuration", "command",
    # Web framework terms
    "makemigrations", "migrate", "migration", "orm", "jwt", "cors",
    "drf", "rest framework", "viewset", "serializer",
    "manage.py", "routing", "signal", "signals",
    "args", "kwargs", "anagram", "anagrams",
    # Python keywords — commonly asked in interviews but were missing
    "yield", "yield keyword", "assert", "assert keyword",
    "del", "del keyword", "global", "global keyword",
    "nonlocal", "nonlocal keyword", "pass", "pass keyword",
    "break", "break keyword", "continue", "continue keyword",
    "raise", "raise keyword", "finally", "finally keyword",
    "with", "with keyword", "as keyword", "elif", "elif keyword",
    "keyword", "keywords",
    "context manager", "metaclass", "abstract",
    "overriding", "overloading", "oops", "oop",
    "http method", "http verb", "http status", "put method", "post method", "get method",
    "patch method", "delete method", "http",
    "merge conflict", "merge conflicts",
    "flask", "fastapi", "nginx",

    # ── Linux (extended) ──────────────────────────────────────────────────────
    "ulimit", "sysctl", "uname", "uptime", "dmesg", "runlevel", "init",
    "systemd", "service", "cgroup", "namespace", "nohup", "screen", "tmux",
    "xargs", "stdin", "stdout", "stderr", "redirect", "pipe",
    "file permission", "file permissions", "sticky bit", "setuid", "setgid",
    "hardlink", "symlink", "symbolic link", "soft link",
    "environment variable", "bashrc", "bash_profile", "profile",
    "boot process", "kernel", "initrd", "grub2", "swap space",
    "lsblk", "blkid", "parted", "mkfs", "mount", "umount", "fstab",
    "cgroup v2", "namespaces", "chroot", "jail", "seccomp",
    "rpm", "dpkg", "snap", "flatpak",
    "ip route", "ip addr", "ip link", "ifconfig", "route",
    "ping", "traceroute", "curl", "wget", "dig", "nslookup", "host",
    "iptables rule", "iptables chain", "iptables table",
    "systemd unit", "service unit", "timer unit",
    "journald", "rsyslog", "syslog",
    "memory management", "virtual memory", "page fault", "oom killer",
    "zombie process", "orphan process", "background process", "foreground process",
    "signal", "sigkill", "sigterm", "sighup", "sigint",
    "nice", "renice", "priority", "scheduling",
    "tmpfs", "procfs", "sysfs",
    "ldd", "objdump", "readelf", "nm", "strip",

    # ── Production Support / ITSM (extended) ─────────────────────────────────
    "sop", "war room", "bridge call", "p1", "p2", "p3",
    "severity", "priority", "triage", "remediation",
    "failback", "disaster recovery", "dr", "bcp",
    "rpo", "rto", "business continuity",
    "escalation matrix", "escalation path",
    "mean time to restore", "mean time to detect", "mean time between failures",
    "alert fatigue", "false positive", "false negative",
    "change freeze", "change window", "maintenance window",
    "rollback plan", "test plan", "deployment plan",
    "capacity planning", "resource utilization",
    "dependency mapping", "impact analysis", "blast radius",
    "heartbeat", "watchdog", "health check",

    # ── DevOps / CI-CD (extended) ─────────────────────────────────────────────
    "github actions", "gitlab ci", "bitbucket pipelines", "circleci", "travis ci",
    "artifact", "artifact registry", "docker registry",
    "sonarqube", "sonar", "code quality", "code coverage", "static analysis",
    "nexus", "artifactory",
    "git flow", "trunk based development", "trunk-based", "feature branch",
    "release branch", "hotfix", "cherry-pick", "rebase", "stash", "squash",
    "gitops", "flux", "fluxcd",
    "vault", "consul", "service discovery",
    "chaos engineering", "game day", "load testing", "stress testing",
    "k6", "locust", "jmeter", "gatling",
    "blue green deployment", "canary deployment", "feature flag", "feature toggle",
    "immutable infrastructure", "infrastructure as code", "iac",
    "shift left", "devsecops", "sast", "dast", "dependency scanning",
    "secret management", "rotation", "least privilege",
    "webhook", "event driven", "event-driven",
    "api gateway", "service mesh", "sidecar proxy", "sidecar",

    # ── SRE (extended) ────────────────────────────────────────────────────────
    "error budget", "burn rate", "availability", "reliability",
    "toil", "golden signals", "four golden signals",
    "saturation", "traffic", "latency percentile", "p99", "p95",
    "alertmanager", "loki", "tempo",
    "distributed tracing", "opentelemetry", "otel", "jaeger", "zipkin",
    "elk stack", "elasticsearch", "logstash", "kibana",
    "splunk", "datadog", "newrelic", "dynatrace", "apm",
    "tracing", "span", "trace id", "correlation id",
    "slo target", "error rate", "latency budget",

    # ── Kubernetes (extended) ─────────────────────────────────────────────────
    "statefulset", "daemonset", "job", "cronjob",
    "pvc", "pv", "persistent volume", "persistent volume claim", "storageclass",
    "rbac", "clusterrole", "clusterrolebinding", "serviceaccount",
    "networkpolicy", "limitrange", "resourcequota",
    "vpa", "vertical pod autoscaler",
    "node affinity", "pod affinity", "taint", "toleration",
    "pod disruption budget", "pdb",
    "admission controller", "validating webhook", "mutating webhook",
    "crd", "custom resource definition", "operator", "controller",
    "init container", "sidecar container",
    "coreDNS", "coredns", "cni", "calico", "flannel", "weave", "cilium",
    "metallb", "cert-manager",
    "etcd", "kube-apiserver", "kube-scheduler", "kube-controller-manager",
    "kubelet", "kube-proxy",
    "kubeconfig", "context", "cluster", "namespace",
    "helm chart", "values.yaml", "chart repository",
    "argo rollouts", "blue-green deployment",
    "pod lifecycle", "container lifecycle", "liveness probe", "readiness probe", "startup probe",
    "resource limits", "resource requests", "cpu limit", "memory limit",
    "node selector", "node pool",
    "oci", "container runtime", "containerd", "cri-o",

    # ── OpenStack (extended) ──────────────────────────────────────────────────
    "horizon", "octavia", "barbican", "ironic", "magnum", "trove",
    "sahara", "manila", "aodh", "ceilometer", "gnocchi", "panko",
    "nova flavor", "nova flavors", "security group", "floating ip", "fixed ip",
    "tenant", "project", "network agent", "metadata agent",
    "l3 agent", "dhcp agent", "ovs agent", "ml2 plugin",
    "provider network", "tenant network", "external network",
    "nova-compute", "nova-api", "nova-conductor", "nova-scheduler",
    "live migration", "cold migration", "evacuate", "shelve",
    "snapshot", "image", "volume type", "availability zone",
    "server group", "affinity", "anti-affinity",
    "token", "endpoint", "catalog", "region",

    # ── Java (extended) ───────────────────────────────────────────────────────
    "heap", "heap space", "stack overflow", "out of memory", "oom",
    "garbage collector", "gc", "g1 gc", "cms", "serial gc", "parallel gc", "zgc", "shenandoah",
    "jit", "jit compiler", "bytecode", "classloader", "class loader",
    "reflection", "annotation", "generic", "generics", "type erasure",
    "collections", "arraylist", "linkedlist", "hashmap", "treemap",
    "hashset", "treeset", "deque", "priority queue",
    "iterator", "iterable", "comparable", "comparator",
    "serializable", "cloneable", "clonenotallowed",
    "checked exception", "unchecked exception", "runtime exception",
    "static", "final", "finally", "abstract class",
    "functional interface", "stream api", "stream", "optional", "completablefuture",
    "executorservice", "thread pool", "threadpoolexecutor",
    "synchronized", "volatile", "atomic", "concurrenthashmap",
    "reentrantlock", "lock", "condition",
    "spring mvc", "spring security", "spring data", "spring cloud",
    "jpa", "jpql", "criteria api", "entity", "repository",
    "jdbc", "connection pool", "hikaricp", "c3p0",
    "mockito", "testng", "powermock", "assertj",
    "singleton pattern", "factory pattern", "builder pattern",
    "observer pattern", "strategy pattern", "facade pattern",
    "design pattern", "solid principle", "solid",
    "immutable class", "thread safety", "race condition",
    "equals hashcode", "equals and hashcode",
    "instanceof", "casting", "autoboxing", "unboxing",
    "string pool", "string interning", "stringbuilder", "stringbuffer",
    "varargs", "enum", "record",
    "java 8", "java 11", "java 17", "java 21", "lts version",
    "module system", "jpms", "jigsaw",
    "war", "jar", "ear", "classpath",
    "tomcat", "jetty", "undertow", "wildfly", "jboss",

    # ── JavaScript / Node.js (extended) ──────────────────────────────────────
    "var", "let", "const",
    "arrow function", "prototype", "prototype chain", "prototypal inheritance",
    "hoisting", "event loop", "call stack", "task queue", "microtask",
    "callback", "callback hell", "promise", "promise chain",
    "async await", "async/await",
    "dom", "document object model", "bom", "window object",
    "queryselector", "getelementbyid", "addeventlistener",
    "fetch api", "xmlhttprequest", "axios",
    "es6", "es2015", "es modules", "commonjs", "require", "module.exports",
    "npm", "yarn", "pnpm", "package.json", "node_modules",
    "webpack", "vite", "babel", "eslint", "prettier",
    "closure", "iife", "immediately invoked",
    "event delegation", "event bubbling", "event capturing",
    "debounce", "throttle",
    "spread operator", "rest parameter", "destructuring",
    "template literal", "tagged template",
    "map", "filter", "reduce", "foreach", "find", "findindex", "some", "every",
    "null", "undefined", "nan", "typeof", "instanceof",
    "symbol", "bigint", "weakmap", "weakset", "weakref",
    "proxy", "reflect",
    "localstorage", "sessionstorage", "indexeddb", "cookies",
    "web worker", "service worker", "worklet",
    "websocket", "sse", "server sent events",
    "cors", "same origin policy", "preflight",
    "jwt", "oauth", "session",
    "single page application", "spa", "ssr", "ssg", "csr",
    "virtual dom", "reconciliation", "fiber",
    "react hooks", "usestate", "useeffect", "usecontext", "usememo", "usecallback", "useref",
    "redux", "context api", "zustand", "recoil",
    "next.js", "getserversideprops", "getstaticprops",
    "typescript interface", "type alias", "generic type",
    "union type", "intersection type", "type guard",
    "optional chaining", "nullish coalescing",

    # ── HTML (extended) ───────────────────────────────────────────────────────
    "html5", "doctype", "semantic html", "semantic element",
    "div", "span", "anchor", "hyperlink",
    "form", "input", "button", "select", "textarea", "label",
    "table", "thead", "tbody", "tr", "td", "th",
    "nav", "header", "footer", "article", "section", "aside", "main",
    "canvas", "svg", "audio", "video", "iframe",
    "meta tag", "viewport", "charset", "open graph",
    "seo", "accessibility", "aria", "aria label", "aria role",
    "data attribute", "custom attribute",
    "web component", "shadow dom", "custom element", "template tag", "slot",
    "lazy loading", "preload", "prefetch", "defer", "async script",
    "html entity", "character encoding",
    "block element", "inline element", "replaced element",

    # ── CSS (extended) ────────────────────────────────────────────────────────
    "css", "selector", "specificity", "cascade", "inheritance",
    "box model", "margin", "padding", "border", "content area",
    "display", "block", "inline", "inline-block", "flex", "grid",
    "flexbox", "flex container", "flex item", "justify content", "align items",
    "css grid", "grid template", "grid column", "grid row", "grid area",
    "media query", "responsive design", "breakpoint", "mobile first",
    "css variable", "custom property",
    "pseudo class", "pseudo element",
    "animation", "transition", "transform", "translate", "scale", "rotate",
    "z-index", "stacking context", "overflow", "clip",
    "float", "clear", "clearfix",
    "position", "absolute", "relative", "fixed", "sticky",
    "bem", "css module", "css-in-js", "styled components",
    "sass", "scss", "less", "preprocessor",
    "viewport unit", "vw", "vh", "vmin", "vmax",
    "calc", "clamp", "min", "max",
    "font face", "web font", "font loading",
    "dark mode", "color scheme", "prefers-color-scheme",
    "css reset", "normalize css",

    # ── SQL / PostgreSQL (extended) ───────────────────────────────────────────
    "ddl", "dml", "dcl", "tcl",
    "select", "insert", "update", "delete", "truncate",
    "create table", "alter table", "drop table",
    "inner join", "left join", "right join", "full outer join", "cross join", "self join",
    "where", "group by", "having", "order by", "limit", "offset",
    "subquery", "correlated subquery", "cte", "with clause", "common table expression",
    "window function", "over", "partition by", "row_number", "rank", "dense_rank",
    "lag", "lead", "first_value", "last_value",
    "aggregate function", "count", "sum", "avg", "min", "max",
    "index", "b-tree index", "hash index", "gin index", "gist index", "brin index",
    "primary key", "foreign key", "unique constraint", "check constraint",
    "not null", "default value",
    "normalization", "first normal form", "second normal form", "third normal form",
    "1nf", "2nf", "3nf", "bcnf", "denormalization",
    "acid", "atomicity", "consistency", "isolation", "durability",
    "transaction", "commit", "rollback", "savepoint",
    "isolation level", "read committed", "repeatable read", "serializable",
    "dirty read", "phantom read", "non-repeatable read",
    "deadlock", "lock", "row lock", "table lock",
    "mvcc", "multiversion concurrency control",
    "vacuum", "autovacuum", "analyze", "pg_stat",
    "explain", "explain analyze", "execution plan", "query plan",
    "query optimization", "query planner",
    "pg_stat_activity", "pg_locks", "pg_indexes",
    "connection pooling", "pgbouncer",
    "streaming replication", "logical replication", "wal", "write ahead log",
    "pitr", "point in time recovery",
    "tablespace", "schema", "view", "materialized view",
    "stored procedure", "stored function", "trigger", "rule", "sequence",
    "jsonb", "json type", "hstore", "array type",
    "full text search", "tsvector", "tsquery", "pg_trgm",
    "pg_cron", "pg_partman", "partitioning", "table partitioning",
    "postgres", "postgresql", "mysql", "sqlite", "oracle", "mssql", "mariadb",
    "nosql", "mongodb", "cassandra", "dynamodb", "firestore",

    # ── Django (extended) ─────────────────────────────────────────────────────
    "django model", "django view", "django template", "mvt",
    "queryset", "manager", "custom manager", "queryset api",
    "select related", "select_related", "prefetch related", "prefetch_related",
    "n+1 problem", "n plus 1",
    "django admin", "admin site", "modeladmin", "inline",
    "form", "model form", "form validation", "clean method",
    "generic view", "class based view", "function based view",
    "listview", "detailview", "createview", "updateview", "deleteview",
    "django middleware", "request middleware", "response middleware",
    "django signal", "post save", "pre save", "post delete",
    "context processor", "template tag", "template filter",
    "static files", "media files", "file upload",
    "django settings", "installed apps", "databases", "secret key",
    "auth user model", "custom user model",
    "django authentication", "login", "logout", "session",
    "csrf", "xss", "sql injection", "django security",
    "drf", "rest framework", "serializer", "viewset", "router",
    "permission class", "throttle", "pagination",
    "django celery", "periodic task", "task queue",
    "django channels", "websocket",
    "django cache", "cache framework", "memcached",
    "django test", "testcase", "request factory", "test client",
    "fixture", "management command", "custom command",
    "django deployment", "gunicorn", "uwsgi", "whitenoise",

    # ── Flask (extended) ──────────────────────────────────────────────────────
    "flask app", "app factory", "blueprint",
    "flask route", "view function", "url rule",
    "request object", "response object", "jsonify", "make response",
    "redirect", "url for", "render template",
    "jinja2", "jinja template", "template inheritance", "block", "extends",
    "before request", "after request", "teardown request",
    "error handler", "404", "500",
    "flask sqlalchemy", "flask-sqlalchemy", "flask migrate",
    "flask login", "flask wtf", "flask restful", "flask jwt",
    "application context", "request context", "g object",
    "wsgi", "werkzeug", "itsdangerous",
    "flask testing", "flask test client",
    "flask config", "config from object", "config from env",
    "flask blueprint", "application factory",
    "flask marshmallow", "flask pydantic",

    # ── Unix / Shell scripting (extended) ─────────────────────────────────────
    # Bash special variables (post-STT-correction form)
    "$#", "$?", "$@", "$*", "$0", "$1", "$2", "$$", "$!",
    "dollar hash", "dollar question mark", "dollar star", "dollar at",
    "dollar zero", "dollar exclamation",
    "bash special variable", "special variable",
    "positional parameter", "positional parameters",
    "exit status", "exit code", "return code", "return value",
    "last exit status",
    # Shell scripting constructs
    "shebang", "#!/bin/bash", "#!/bin/sh",
    "heredoc", "here document", "here string",
    "subshell", "subprocess",
    "backtick", "command substitution", "process substitution",
    "pipe", "named pipe", "fifo",
    "stdin", "stdout", "stderr",
    "file descriptor", "file descriptors",
    "redirect", "redirection", "append redirect",
    "set -e", "set -x", "set -u", "set -o", "pipefail",
    "errexit", "nounset", "xtrace",
    "trap", "signal handler",
    "job control", "foreground", "background", "nohup",
    "umask", "umask value",
    # Unix text processing — sed/awk/grep
    "sed", "awk", "xargs",
    "in-place edit", "in place edit",
    "substitute", "substitution",
    "field separator", "record separator",
    "begin block", "end block",
    "regular expression", "extended regex",
    "grep -v", "grep -r", "grep -i", "grep -n",
    "sed -i", "sed s command",
    "awk begin", "awk end", "awk print",
    "line range", "address range",
    "nth line", "line number",
    "pattern space", "hold space",
    "append", "prepend",
    # Unix file/permission concepts
    "hard link", "soft link", "symbolic link", "symlink",
    "inode number",
    "file permission", "octal permission",
    "chmod", "chown", "chgrp",
    "sticky bit", "setuid", "setgid", "suid", "sgid",
    "umask", "acl", "access control list",
    "posix", "posix standard",
    # Process/signal management
    "zombie process", "orphan process",
    "sigkill", "sigterm", "sighup", "sigint", "sigusr1",
    "kill signal", "trap signal",
    "wait command", "wait builtin",
    # Shell builtins / scripting
    "read command", "read builtin",
    "declare", "typeset", "local variable",
    "array in bash", "bash array", "associative array",
    "string manipulation", "substring",
    "parameter expansion", "variable expansion",
    "brace expansion", "globbing",
    "wildcard", "glob pattern",
    "case statement", "select statement",
    "function in bash", "shell function",
    "source command", "dot command",
    "eval command",
    "getopts", "getopt",
    "debug mode", "bash debug",
    "shell option",
    # Unix networking
    "nc", "netcat", "socat",
    "unix socket", "domain socket",
    # Unix performance
    "perf command", "strace", "ltrace",
    "dstat",
}

# Precompute single-word vs multi-word sets for efficient word-boundary matching
# Single-word terms use word-boundary checks (avoids "try"→"trying", "set"→"reset" false matches)
# Multi-word terms still use substring search
TECH_TERMS_SINGLE = frozenset(t for t in TECH_TERMS if ' ' not in t)
TECH_TERMS_MULTI = [t for t in TECH_TERMS if ' ' in t]


def _has_tech_term(lower: str) -> bool:
    """Check for tech terms using word boundaries (prevents substring false positives)."""
    # Word-tokenize (keep slashes for ci/cd; dots removed so 'nginx.' matches 'nginx')
    words = set(re.sub(r"[^\w/]", ' ', lower).split())
    if words & TECH_TERMS_SINGLE:
        return True
    # Check dotted/special-char terms directly as substrings
    # (e.g. "manage.py", "$#", "$?", "#!/bin/bash")
    for t in TECH_TERMS_SINGLE:
        if ('.' in t or '$' in t or '#' in t or '!' in t) and t in lower:
            return True
    return any(t in lower for t in TECH_TERMS_MULTI)


INCOMPLETE_ENDINGS = {
    "and", "or", "but", "the", "a", "an", "of", "to", "with", "for",
    "in", "on", "at", "between", "is", "are", "was", "were",
    "can", "could", "would", "should", "will", "do", "does",
}

IGNORE_PATTERNS = [
    # Fillers and acknowledgements
    r"^(okay|ok|alright|sure|yes|no|yeah|right|hmm|um|uh)[\s,.!?]*$",
    r"^(great|good|nice|perfect|thanks|thank you)[\s,.!?]*$",
    # Audio/screen checks
    r"can you hear me", r"is (this|my audio) working",
    r"one (moment|second|minute)", r"let me (think|see|check)",
    r"share.*screen", r"open.*link", r"click on", r"mute",
    r"Microsoft Office Word.*", r"Word\.Document.*", r"MSWordDoc",
    r"you're on mute", r"can you see my screen",
    # Greetings and small talk (not interview questions)
    r"^(hi|hello|hey|bye|goodbye),?\s*([\w]+)?[.!,]?\s*(good\s*(morning|evening|afternoon|night))?[.!,]?\s*$",
    r"^good\s*(morning|evening|afternoon|night)[.!,]?\s*$",
    r"^(hi|hello|hey),?\s*\w+\.\s*(good\s*(morning|evening|afternoon))?",
    r"^(hi|hello|hey),?\s*\w+\.\s*(bye|goodbye),?\s*\w+",
    # Camera, physical, and setup instructions
    r"come\s*(on\s*)?to\s*(the\s*)?camera",
    r"move\s*towards", r"move\s*to\s*(your|the)\s*(left|right)",
    r"place\s*(any|a|the)\s*table", r"table\s*or\s*something",
    r"(face|eye)\s*contact", r"not\s*able\s*to\s*see\s*(the|your)\s*face",
    r"(above|below|behind)\s*light", r"light\s*is\s*there",
    r"(your|the)\s*(camera|webcam|video)\s*(is|position|focus|angle)",
    r"focus\s*slide\s*position", r"slide\s*position",
    # Physical instructions: light, holding, positioning
    r"light\s*is\s*coming\s*from\s*(the\s*)?(top|bottom|side|above|behind)",
    r"(your\s*)?face\s*(is\s*)?(really\s*)?(not\s*)?(visible|clear|showing)\b",
    r"can\s*you\s*hold\s*(it|the|this)\b",
    r"(hold|keep)\s*(it|the|this|camera|laptop|phone|light)\s*(up|down|on\s*top|there|still|higher|lower)",
    r"\blittle\s*(more|higher|lower|up|down)\s*[?,]",
    r"take\s*(a\s*)?(snapshot|photo|picture|screenshot)\s*(of|now)?",
    r"keep\s*(the\s*)?(laptop|camera|phone|light|it)\s*(down|up|there|now)\b",
    r"(adjust|position|fix)\s*(your\s*)?(camera|light|lighting)\b",
    r"sit\s*(to|towards)\s*(the\s*)?(light|camera|left|right)",
    # End-of-interview chatter
    r"(we\s*will\s*)?let\s*you\s*know\b",
    r"thank\s*you\s*for\s*(your\s*)?time",
    r"any\s*questions\s*(from|for)\s*(you|your\s*side)",
    # Recording/compliance setup
    r"record\s*(the|this)\s*session", r"hope\s*you.*(re|are)\s*comfortable",
    r"compliance\s*and\s*audit", r"as\s*it\s*is\s*a\s*compliance",
    # Coordinator/scheduling talk
    r"waiting\s*for\s*(your|the)\s*confirmation",
    r"(is\s*it|that)\s*fine\??\s*(can\s*I|shall)", r"can\s*I\s*change\s*the\s*time",
    r"getting\s*another\s*call", r"stopped\s*recruiting",
    r"want\s*(to|you)\s*arrange", r"you\s*want\s*(to|any)\s*changes",
    r"(I|we)\s*(have|are)\s*done\s*from\s*(my|our)\s*side",
    r"anything\s*else\s*from\s*(your|his|her)\s*side",
    r"now\s*it'?s?\s*perfect", r"it\s*is\s*like\s*not\s*good",
    r"last\s*time.*said.*good\s*now",
    # Non-question interviewer statements (not directed at candidate)
    r"^(so|okay),?\s*\w+,?\s*we\s*(are|were)",
    r"^yeah,?\s*(yeah,?\s*)?now",
    # Transitional / setup statements — interviewer speaking to themselves or setup
    r"^(alright|alwrite|okay|ok|right|so|hey|now),?\s+(a|the|an|this|that)\s+(function|class|code|method|script)\s+(defined|like|to|in|at|that|of|with|from|below|above|follows)\b",
    r"^(let'?s|let me)\s+see\s+(how|what|if|whether)\b",
    r"^(let'?s|let me)\s+(start|begin|move|go|continue|proceed|check|run|test)\b",
    r"^(let'?s|let me)\s+(look|try|do|use|take)\s+(a|an|this|the|that|it)\b",
    # Only reject "we'll use/start/look/check" — NOT "write/create" (those can be code requests)
    r"^(we'?ll|we will|we can|we should)\s+(build|use|start|begin|look|check)\b",
    # Reject "we'll write/create a function/class/method TO ..." (interviewer demo, not question)
    r"^(we'?ll|we will)\s+(write|create)\s+(a|an|the)?\s*(function|class|method|example|code)\s+to\b",
    r"^(we'?re going to|i'?m going to|going to)\s+(write|look|check|see|find|use)\b",
    r"^(i|we)\s+will\s+(write|find|define|create|build|make)\s+(a|an|the)\s+(function|class|method|code)\b",
    # End-of-turn filler statements (common after interviewer finishes speaking)
    r"^(and\s+)?that'?s\s+(it|all|done|correct|right|about\s+it)[\s.!]*$",
    r"^(and\s+)?that'?s\s+about\s+(it|all)[\s.!]*$",
    r"^(and\s+)?i'?m\s+done[\s.!]*$",
    r"^(like,?\s+)?(the\s+)?(devops|python|django)\s+script[\s.!]*$",
    r"^like,?\s+the\s+\w+\s+script[\s.!]*$",
    r"^i'?m\s+trying\s+to\s+(get|find|do|make)\s+it\b",
    r"^(just\s+)?follow\s+up\b",
    r"^i\s+(just\s+)?wanted\s+to\b",
    r"^i\s+have\s+(a|the)\s+(function|method|code|program)\s+to\s+\w+\b",
    r"^i\s+have\s+written\s+(a|the)\b",
    # Meeting platform notifications (Google Meet, Teams, Zoom)
    r"joined the (meeting|conversation|call)",
    r"left the (meeting|conversation|call)",
    r"(meeting|call)\s*(started|ended|recorded)",
    r"recording\s*(started|stopped|in progress)",
    r"is presenting",
    r"named the meeting",
    r"created this meeting",
    r"muted their (microphone|mic)",
    # Noise / background chatter — short phrases ending with "sir" (not a question)
    r"\bsir[\s.!?]*$",
    r"^(news|new)\s+service\s+to\s+the\s+sir",
    r"^(look\s+at\s+this|stands?\s+in\s+aws|root\s+apple)[\s.!?]*$",
    # Transliterated Telugu filler that leaks through with en-IN mode
    r"^(tammane?|tamma)\b",
    r"^foreign\s+rum\s+sambandhinchi",
    # Google Meet UI / system notifications injected via chat
    r"raising your hand",
    r"lowering your hand",
    r"developing an extension for meet",
    r"an add-on would work better",
    r"stand.?up meeting",
    r"daily standup",
    r"scrum (meeting|call)",
    r"join.*meeting|meeting.*link|meeting.*id",
    r"^\w+developers?\s+(stand\s*up|standup|daily)",
    r"google meet (is|will|has)",
    r"(someone|everyone)\s+(joined|left)\s+the",
    r"you are (now|the) (presenting|host|co-host)",
    r"your (microphone|camera|mic)\s+(is|was)\s+(muted|unmuted|on|off)",
    r"pinned (a message|note)",
    r"(host|co-host)\s+(has|have)\s+(muted|removed|added)",
    # Clearly misheard / inappropriate STT output — reject without answering
    r"\bsex\b",
    r"\bporn\b",
    r"\bnude\b",
    r"\bexplain about sex\b",
    r"\btell me about sex\b",

    # ── Code execution output captured by STT ──────────────────────────────
    # These are Python/shell RESULTS being narrated, not interview questions
    r"^the\s+result\s+is\b",               # "The result is true/false/..."
    r"^(the\s+)?output\s+is\b",            # "Output is..." / "The output is..."
    r"^output\s*[:：]",                    # "Output: True"
    r"^(it\s+)?returns?\s+(true|false|none|[\[\{]|\d)",   # "returns True", "returns [1,2,3]"
    r"^this\s+returns\b",                  # "This returns..."
    r"^the\s+output\s+(will|would|should)\s+be\b",
    r"^(so\s+)?the\s+(answer|value|result|output)\s+(is|will\s+be|would\s+be)\b",
    r"^(true|false|none)\s*[\.,!]?\s*$",   # bare "True." or "False."
    r"^(true|false|none)\s+(is\s+)?(printed|returned|shown|displayed)",
    r"^print\s*\(",                        # "print(..." — code being narrated
    r"^>>>\s*\w",                          # ">>> " — Python REPL prompt
    r"^in\s*\[[\d]+\]:",                   # "In [1]:" — Jupyter prompt
    r"^out\s*\[[\d]+\]:",                  # "Out[1]:"
    r"^\d+\s*$",                           # bare number output "42"
    r"^\[\s*\d",                           # list output "[1, 2, 3]..."
    r"^\{[\'\"]",                          # dict output "{'key':..."
    # ── Narrated code walk-through by interviewer ──────────────────────────
    r"^(here|now)\s+(we\s+)?(define|defined|have|create|call|pass|check|declare)\b",
    r"^(we\s+are\s+)?(passing|calling|checking|iterating|looping|returning)\b",
    r"^(this\s+)?(line|function|variable|method|class|loop)\s+(is|does|returns|will|checks|takes)\b",
    r"^(if\s+the\s+)?(condition|variable|value|number|string|list|array)\s+(is|equals|matches|becomes)\b",
    r"^(so\s+)?(here|above|below)\s+(we|the\s+function|this)\s+(can\s+see|see|have|shows?)\b",
    r"^(this|that)\s+(will|would)\s+(print|return|output|give|show|produce)\b",
    r"^(the\s+)?(first|second|third|last|next)\s+(element|item|value|index|iteration|line|step)\b",
    r"^(for|while)\s+\w+\s+in\s+\w+",     # Python loop being narrated: "for x in arr..."
    r"^i\s+(am\s+)?(using|calling|passing|checking|iterating|defining)\b",
    # ── Very short / incomplete sentences ─────────────────────────────────
    r"^(the\s+)?(script|code|function|program|method|class)\s+(for|to|that|which|of)\s*$",
    r"^(a|the|this|that|an)\s+(script|code|function)\s+(is|does|will)\b",
    # ── Common noise / interviewer narration ──────────────────────────────
    r"^(now|so|okay),?\s+(let'?s?\s+)?(run|execute|test|check|verify|call|print)\b",
    r"^(let'?s?\s+)?(run|execute|test|verify)\s+(this|the|it|our|a)\b",
    r"^(you\s+can\s+see|as\s+you\s+can\s+see|now\s+you\s+can\s+see)\b",
    r"^(notice|observe)\s+(that|here|how|the)\b",
    # ── Generic filler / non-question sentences ───────────────────────────
    r"^whatever\s+(is\s+there|you\s+(say|want|like)|it\s+is|the\s+case)[\s.!?]*$",
    r"^today\s+it'?s?\s+a?\s*(bit|little|kind|sort)\s+of\b",  # "Today it's a bit of..."
    r"^(come\s+on|carry\s+on|move\s+on|go\s+on)[\s.!?,]*$",
    r"^(next|previous|skip|continue|proceed)[\s.!?,]*$",
    r"^(i\s+don'?t\s+know|i\s+am\s+not\s+sure|not\s+sure\s+about)[\s\w.!?,]*$",
    # "Sit and suck" / STT garbage short sentences
    r"^[A-Z][a-z]+\s+(and|or)\s+[a-z]+\.$",   # "Sit and suck." — 3-word garbage
    # ── LeetCode / coding platform context narration ───────────────────────
    r"^(given\s+an?\s+(array|list|string|number|integer)|given\s+a\s+sorted)\b",  # BUT only if very short
    r"^(input|output)\s*[:：]\s*([\[\{'\"\d]|true|false|null|none)",  # "Input: [1,2,3]"
    r"^(example|test\s*case)\s+\d*\s*[:：]",                         # "Example 1:"
    r"^constraints\s*[:：]",                                          # "Constraints:"
]

COMPILED_IGNORE = [re.compile(p, re.IGNORECASE) for p in IGNORE_PATTERNS]


# =============================================================================
# HALLUCINATION DETECTION
# =============================================================================

def is_hallucination(text: str) -> bool:
    """Detect Whisper hallucinations (repeated phrases during silence)."""
    if not text or len(text) < 20:
        return False

    lower = text.lower().strip()
    words = lower.split()

    if len(words) > 15:
        unique = len(set(words))
        if unique < 5:
            return True
        if len(words) / unique > 3:
            return True

    parts = [p.strip() for p in lower.split(',') if p.strip()]
    if len(parts) >= 3:
        counts = Counter(parts)
        if counts.most_common(1)[0][1] >= 3:
            return True

    if len(words) >= 8:
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
        bigram_counts = Counter(bigrams)
        if bigram_counts.most_common(1)[0][1] >= 4:
            return True

    return False


# =============================================================================
# MAIN VALIDATION
# =============================================================================

def validate_question(text: str) -> Tuple[bool, str, str]:
    """
    Validate if text is an interview question.
    Returns: (is_valid, cleaned_text, rejection_reason)
    """
    if not text:
        return False, "", "empty"

    text = text.strip()

    # Strip common noise prefixes FIRST (before STT corrections, so corrections
    # can match start-of-string patterns like ^right\s+here)
    noise_prefixes = [
        r"^(?:async\s+out\s+there|out\s+there|over\s+there)\.?\s*",
        r"^(?:yeah|yes|okay|ok|so|and|but|now)\s*,?\s+(?=[A-Z])",
    ]
    for prefix_pattern in noise_prefixes:
        text = re.sub(prefix_pattern, "", text, flags=re.IGNORECASE).strip()

    # Strip trailing Telugu/regional language noise that Sarvam sometimes appends
    # e.g. "What is polymorphism? Aa ee oka polymorphism." → "What is polymorphism?"
    # Pattern: after a sentence-ending punctuation, remove junk non-English fragments
    text = re.sub(
        r'([.?!])\s+[A-Za-z]{1,3}\s+[A-Za-z]{1,3}\s+\w+\s+\w+[.?!]?\s*$',
        r'\1', text
    ).strip()
    # Remove standalone transliterated filler at end (e.g. "Aa ee oka ...")
    text = re.sub(
        r'\s+[Aa][a-z]?\s+[Ee][a-z]?\s+\w+\s+\w+\.?\s*$',
        '', text
    ).strip()

    # Early gibberish check — reject number-start BEFORE STT corrections convert it
    # e.g. "10 for file man list for example" would otherwise be converted to
    # "Give an example of 10 for file man list" and pass the later number check
    if re.match(r'^\d+\s+\w', text.strip()):
        return False, "", "gibberish_number_start"

    # Apply STT corrections AFTER noise stripping
    text = apply_stt_corrections(text)

    if is_hallucination(text):
        return False, "", "hallucination"

    if is_youtube_or_tutorial(text):
        return False, "", "youtube_tutorial"

    for pattern in COMPILED_IGNORE:
        if pattern.search(text):
            return False, "", "ignore_pattern"

    words = text.split()
    if len(words) < 2:
        return False, "", "too_short"

    # Reject gibberish starting with numbers (e.g. "10 for file man list")
    if re.match(r'^\d+\s+\w', text.strip()):
        return False, "", "gibberish_number_start"

    # Reject vague pronoun-only questions (e.g., "How do you implement it?")
    if is_vague_question(text):
        return False, "", "vague_pronoun_only"

    lower = text.lower()
    # A starter matches if it's at the start of the sentence,
    # OR appears mid-sentence — but only for starters >= 5 chars to prevent
    # short phrases like "is there" / "are there" from matching inside unrelated sentences
    # e.g. "Whatever is there." must NOT count as having a starter.
    has_starter = any(
        lower.startswith(s) or (len(s) >= 5 and f" {s}" in lower)
        for s in QUESTION_STARTERS
    )
    has_tech = _has_tech_term(lower)

    # HR/behavioral patterns — check BEFORE incomplete check so "worked on" doesn't block
    _hr_early = ["challenging project", "difficult project", "hard project", "worked on",
                 "yourself", "your experience", "notice period", "salary", "ctc",
                 "strengths", "weaknesses", "strength and weakness", "five years", "5 years",
                 "hobbies", "why this company", "why do you want", "looking for change",
                 "greatest achievement", "proud of", "handle pressure", "team conflict",
                 "tell me about", "walk me through", "current role", "day to day",
                 "production issue", "production support", "production problem",
                 "handle production", "handle server", "handle incident",
                 "handle on call", "handle escalation", "handle outage"]
    if any(p in lower for p in _hr_early):
        has_tech = True

    last_word = words[-1].rstrip("?.,!").lower()
    if last_word in INCOMPLETE_ENDINGS and not text.endswith("?") and not has_tech:
        return False, "", "incomplete"

    # Dangling-verb short question: "How do you handle?" — missing the object
    # e.g. interviewer paused mid-sentence; save as fragment for next chunk to complete
    _DANGLING_VERBS = frozenset({
        "handle", "fix", "debug", "deploy", "implement", "solve", "manage",
        "monitor", "troubleshoot", "resolve", "address", "approach", "tackle",
        "migrate", "configure", "install", "maintain", "secure", "test",
        "do", "build", "create", "design", "optimize", "improve",
    })
    if len(words) <= 5 and "?" in text and last_word in _DANGLING_VERBS:
        return False, text, "incomplete"

    has_question_mark = "?" in text

    # Reject very short questions with no tech term (e.g. "What is?", "How?")
    # "What is Java?" (3 words, has_tech=True) must pass — only reject if no tech term
    if len(words) <= 3 and not has_tech and has_question_mark:
        return False, "", "too_vague"
    # Single-word content queries like "What is X?" where X is a plain noun with no tech term
    # are still rejected above; nothing extra needed here

    # Reject questions with only vague/filler words and no tech term
    vague_filler = {"what", "about", "the", "other", "one", "that", "this",
                    "it", "those", "these", "how", "why", "where", "which",
                    "is", "are", "do", "does", "can", "could", "would", "should",
                    "a", "an", "of", "for", "and", "or", "some", "any", "just"}
    content_words = [w.rstrip("?.,!") for w in words if w.rstrip("?.,!").lower() not in vague_filler]
    if not content_words and not has_tech:
        return False, "", "too_vague"

    # HR/behavioral interview questions count as interview-relevant
    hr_patterns = ["yourself", "your experience", "your background", "your responsibility",
                   "looking for change", "looking for a change", "why are you leaving",
                   "still working", "left this organization", "left this company",
                   "left this job", "left your previous", "left your last",
                   "have you left",
                   "notice period", "current ctc", "expected ctc", "salary",
                   "years of experience", "why do you want", "strengths", "weaknesses",
                   "strength and weakness", "strength weakness",
                   "tell me about", "walk me through", "your role", "your team",
                   "current organization", "previous organization", "latest organization",
                   "go ahead about", "about your",
                   "where do you see", "five years", "5 years", "career goal",
                   "hobbies", "outside work", "passion", "motivation",
                   "why this company", "why this role", "why this position",
                   "greatest achievement", "proud of",
                   "challenging project", "difficult project", "hard project", "worked on",
                   "team conflict", "disagreement with", "handle pressure",
                   "work from home", "remote work", "current salary", "expected salary",
                   "joining date", "when can you join", "available to join",
                   "current role", "day to day", "daily work", "day-to-day"]
    if any(p in lower for p in hr_patterns):
        has_tech = True

    is_coding_question = False
    if re.search(r'\b\w+\s*=\s*\[', text):
        is_coding_question = True
    if "find" in lower:
        is_coding_question = True
    coding_words = ['sort', 'reverse', 'sum', 'max', 'min', 'count', 'even', 'odd', 'prime', 'duplicate',
                    'fibonacci', 'palindrome', 'missing', 'largest', 'smallest', 'average',
                    'slicing', 'slice', 'comprehension', 'factorial', 'swap', 'matrix',
                    'binary', 'search', 'linked', 'stack', 'queue', 'hash', 'tree']
    if any(w in lower for w in coding_words):
        is_coding_question = True

    # Interview relevance check: reject casual/setup questions with no tech content
    # Only include words that are UNAMBIGUOUSLY non-interview (no double meanings)
    non_interview_words = {'camera', 'webcam', 'table', 'arrange', 'comfortable',
                           'recording', 'audible', 'visible', 'mute', 'unmute',
                           'confirmation', 'slide position', 'focus slide',
                           'sit', 'stand'}
    non_interview_phrases = [
        'come on to the camera', 'move towards', 'place any table',
        'eye contact', 'above light', 'is it fine', 'want to arrange',
        'from my side', 'from your side', 'anything else from',
        'now it\'s perfect', 'not good properly', 'can I change the time',
    ]
    has_non_interview = (any(w in lower for w in non_interview_words) or
                         any(p in lower for p in non_interview_phrases)) and not has_tech

    # "X versus Y" / "X vs Y" comparisons — always interview questions if tech terms present
    has_comparison = bool(re.search(r'\b(versus|vs\.?)\b', lower))

    # Words that strongly indicate NON-IT questions (food, household, nature, casual).
    # These are unambiguous — none of them are tech homonyms.
    _CLEARLY_NON_IT = frozenset({
        'seasoning', 'recipe', 'cooking', 'baking', 'grilling', 'frying',
        'food', 'lunch', 'dinner', 'breakfast', 'meal', 'restaurant',
        'vegetable', 'fruit', 'spice', 'sauce', 'soup', 'salad',
        'weather', 'rain', 'snow', 'cloud9', 'sunshine', 'temperature',
        'sport', 'football', 'cricket', 'basketball', 'tennis',
        'movie', 'film', 'music', 'song', 'dance', 'fashion',
        'hairstyle', 'makeup', 'clothes', 'travel', 'vacation', 'holiday',
        'marriage', 'wedding', 'birthday', 'anniversary',
        'astrology', 'horoscope', 'meditation', 'yoga',
    })
    # "What is [X]?" or "Explain [X]" with a question starter but NO tech term
    # AND the main content word is clearly non-IT → reject to prevent LLM hallucination
    if has_starter and not has_tech and not is_coding_question:
        # Tokenize the question subject (words after the starter)
        _subject_words = set(re.sub(r"[^\w]", ' ', lower).split()) - {
            'what', 'is', 'are', 'the', 'a', 'an', 'how', 'why', 'does',
            'do', 'explain', 'describe', 'define', 'tell', 'me', 'about',
            'in', 'python', 'linux', 'of', 'for', 'and', 'difference',
            'between', 'give', 'example'
        }
        if _subject_words & _CLEARLY_NON_IT:
            return False, "", "non_it_question"

    if has_starter and has_tech:
        pass
    elif has_question_mark and len(words) >= 3 and not has_non_interview:
        # Extra guard: if has ? but NO tech term AND NO IT-adjacent word → reject
        # Prevents "What is auto seasoning?" from passing after STT correction fails
        if not has_tech and not is_coding_question:
            # Allow only if the question has a word that could plausibly be IT
            _IT_ADJACENT = frozenset({
                'auto', 'service', 'server', 'system', 'network', 'application',
                'app', 'software', 'hardware', 'protocol', 'request', 'response',
                'data', 'database', 'storage', 'compute', 'virtual', 'instance',
                'deploy', 'deployment', 'build', 'release', 'version', 'update',
                'security', 'access', 'permission', 'role', 'user', 'admin',
                'log', 'logs', 'error', 'exception', 'debug', 'monitor',
                'performance', 'latency', 'throughput', 'capacity', 'load',
                'backup', 'restore', 'recovery', 'failover', 'redundancy',
                'sync', 'async', 'concurrent', 'parallel', 'distributed',
                'api', 'endpoint', 'webhook', 'integration', 'interface',
                'code', 'program', 'programming', 'language', 'framework',
                'library', 'package', 'dependency', 'module', 'class',
                'object', 'function', 'method', 'variable', 'parameter',
                'algorithm', 'structure', 'pattern', 'design',
                'file', 'directory', 'path', 'command', 'terminal',
                'port', 'socket', 'connection', 'protocol', 'ip',
                'cpu', 'memory', 'disk', 'ram', 'cache', 'buffer',
                # Python keywords — must be IT-adjacent so "yield keyword" questions pass
                'yield', 'assert', 'nonlocal', 'global', 'raise', 'finally',
                'keyword', 'keywords', 'purpose', 'use', 'usage', 'importance',
                'del', 'pass', 'break', 'continue', 'with', 'elif', 'lambda',
                # Bash special variables (word form, before STT correction)
                'dollar', 'hash', 'bash', 'shell', 'script',
                'argument', 'arguments', 'parameter', 'exit',
                'shebang', 'heredoc', 'subshell', 'redirect',
                'stdin', 'stdout', 'stderr', 'descriptor',
                # Linux / production
                'permission', 'daemon', 'kernel', 'signal', 'process',
                'disk', 'memory', 'cpu', 'swap', 'inode', 'mount',
                'runlevel', 'boot', 'cgroup', 'namespace', 'socket',
                'pid', 'thread', 'scheduling', 'priority',
                # DevOps / SRE / Kubernetes
                'deployment', 'pod', 'node', 'cluster', 'replica',
                'probe', 'secret', 'volume', 'ingress', 'namespace',
                'pipeline', 'artifact', 'registry', 'chart', 'operator',
                'budget', 'availability', 'reliability', 'toil',
                'tracing', 'span', 'metric', 'alert', 'saturation',
                # Java
                'heap', 'collector', 'classloader', 'bytecode',
                'generic', 'annotation', 'reflection', 'serialization',
                'iterator', 'comparable', 'functional', 'stream',
                'singleton', 'factory', 'builder', 'observer', 'pattern',
                # JavaScript / HTML / CSS
                'dom', 'event', 'callback', 'promise', 'prototype',
                'hoisting', 'closure', 'scope', 'selector', 'specificity',
                'flexbox', 'grid', 'viewport', 'responsive', 'animation',
                'transition', 'transform', 'media', 'query', 'breakpoint',
                # SQL / DB
                'join', 'index', 'transaction', 'isolation', 'constraint',
                'normalization', 'replication', 'vacuum', 'partition',
                'trigger', 'procedure', 'view', 'sequence', 'window',
                # Django / Flask
                'queryset', 'migration', 'signal', 'middleware', 'blueprint',
                'template', 'serializer', 'permission', 'throttle',
                'wsgi', 'gunicorn', 'jinja',
            })
            content_tokens = set(re.sub(r"[^\w]", ' ', lower).split()) - {
                'what', 'is', 'are', 'the', 'a', 'an', 'how', 'why',
                'does', 'do', 'explain', 'describe', 'tell', 'me',
                'about', 'in', 'of', 'for', 'and', 'or', 'to',
            }
            if not (content_tokens & _IT_ADJACENT):
                return False, "", "non_it_question"
    elif has_starter and len(words) >= 2 and not has_non_interview:
        pass
    elif has_tech and len(words) >= 4:          # lowered from 6 → catch short tech Qs
        pass
    elif has_comparison and len(words) >= 3:    # "Spot vs on-demand vs reserved"
        pass
    elif is_coding_question:
        pass
    elif re.search(r'\bfor\s+example\b|\bas\s+an?\s+example\b', lower):
        # "X for example" / "X as an example" → convert to valid question
        topic = re.sub(r'\s+(for|as)\s+an?\s+example.*$', '', text, flags=re.IGNORECASE).strip()
        return True, f"Give an example of {topic}?", ""
    else:
        return False, "", "no_question_pattern"

    cleaned = text[0].upper() + text[1:] if len(text) > 1 else text

    if has_starter and not cleaned.endswith(("?", ".", "!")):
        cleaned += "?"

    return True, cleaned, ""


def clean_and_validate(text: str) -> Tuple[bool, str, str]:
    """Alias for validate_question."""
    return validate_question(text)


def is_valid_interview_question(text: str) -> bool:
    """Simple boolean check."""
    is_valid, _, _ = validate_question(text)
    return is_valid


# =============================================================================
# QUESTION SPLITTING
# =============================================================================

# Strips noise/filler prefix before a real question
# Handles: "Okay, ...", "Just a moment, please. How to...", "Hold on. What is..."
_OKAY_PREFIX_RE = re.compile(
    r'^.{0,80}?\.\s+(?:okay|ok|alright|right)[,.]?\s+',
    re.IGNORECASE
)
_WAIT_PREFIX_RE = re.compile(
    r'^(?:just a (?:moment|second|min)|one moment|hold on|wait|please wait|sorry|excuse me|pardon)[.,!]?\s+(?:please[.,]?\s+)?',
    re.IGNORECASE
)

def split_merged_questions(text: str) -> str:
    """Extract the best question from merged audio."""
    if not text:
        return text

    text = text.strip()

    # Strip "just a moment / hold on" prefix before the real question
    # e.g. "Just a moment, please. How to check logs?" → "How to check logs?"
    m = _WAIT_PREFIX_RE.match(text)
    if m:
        text = text[m.end():].strip()
        if text and text[0].islower():
            text = text[0].upper() + text[1:]

    # Strip garbage noise that precedes "Okay," mid-sentence.
    # e.g. "Comma, this is the cloth. Okay, top command is used for?"
    #   → "top command is used for?"
    m = _OKAY_PREFIX_RE.match(text)
    if m:
        text = text[m.end():].strip()
        if text and text[0].islower():
            text = text[0].upper() + text[1:]

    def _trim_at_first_complete_question(t: str) -> str:
        """Return only the first complete question from merged text.

        Handles two merged questions: "Q1? Q2." → "Q1?"
        But preserves compound single questions: "What is X and how do Y?" (? at end).
        """
        q_idx = t.find('?')
        # Only trim if '?' appears before 80% of the text (not at the very end)
        if q_idx == -1 or q_idx >= len(t) * 0.8:
            return t
        trailing = t[q_idx + 1:].strip()
        if not trailing:
            return t
        # Trailing is a second question → trim to first question only
        # Trailing is answer echo → trim to first question only
        # Either way: trim at '?'
        return t[:q_idx + 1].strip()

    # Step 1: Find QUESTION_STARTER positions in the current text.
    lower = text.lower()
    positions = []
    for starter in QUESTION_STARTERS:
        idx = 0
        while True:
            pos = lower.find(starter, idx)
            if pos == -1:
                break
            if pos == 0 or text[pos - 1] in ' ,.':
                positions.append((pos, starter))
            idx = pos + 1

    if not positions:
        return text

    positions.sort()

    # Step 2: Strip noise prefix if the first question starter is not at pos=0.
    # ONLY strip when the prefix is separated by a PERIOD (not just a comma).
    # e.g. "India mein business. What is a generator?" → strip "India mein business."
    # But "It is slow, how do you troubleshoot?" → keep the full context (comma only)
    if positions[0][0] > 0:
        prefix = text[:positions[0][0]]
        # Only strip if there's a period in the prefix (clear sentence boundary)
        if '.' in prefix:
            for pos, starter in positions:
                candidate = text[pos:].strip()
                if len(candidate.split()) >= 4:
                    return _trim_at_first_complete_question(candidate)
        return text

    # Step 3: Text starts with a question (positions[0][0] == 0).
    # If there's only ONE question starter, text is already clean — just trim echo.
    if len(positions) < 2:
        return _trim_at_first_complete_question(text)

    # Step 4: Two+ question starters and text starts with the first one.
    # e.g. "What is Route 53 and how do you use it?" → keep whole (single ? at end)
    # e.g. "What is a generator? Explain metaclass." → return first Q only
    return _trim_at_first_complete_question(text)


_is_whisper_hallucination = is_hallucination


def is_code_request(text: str) -> bool:
    """Check if question explicitly asks for code/script output.

    Must be conservative - only trigger for clear "write code" requests,
    not for questions that mention code-related words in passing.
    """
    if not text:
        return False
    lower = text.lower().strip()

    # Example requests ALWAYS expect code — check BEFORE explanation_triggers early-exit
    # e.g. "explain polymorphism and give an example for that"
    # e.g. "what is decorator and give me the example by writing the code"
    if re.search(r'\b(give|show|write)\b.{0,40}\bexample\b', lower):
        return True
    if re.search(r'\bexample\b.{0,20}\b(of|for)\b', lower):
        return True
    if re.search(r'\bwith\s+(an?\s+)?example\b', lower):
        return True

    # If the user explicitly asks for an explanation, it's NOT a code request
    explanation_triggers = [
        "explain", "describe", "concept", "theory", "what is the difference",
        "difference between", "what is", "what are", "what does", "how does",
        "how do", "how to deploy", "how to set up", "how to configure",
        "how to monitor", "how to troubleshoot", "how to scale",
        "how to manage", "how to handle", "how to secure",
        # Generic "how to X" command questions (grep/find/awk/etc.) → NOT code
        "how to search", "how to find", "how to filter", "how to match",
        "how to grep", "how to check", "how to count", "how to list",
        "how to display", "how to show", "how to print",
        "how to sort", "how to remove", "how to delete",
        "how to read", "how to parse", "how to extract",
        # Unix/shell command tasks — answer = command, NOT code
        "how to replace", "how to substitute",
        "how to rename", "how to move",
        "how to copy", "how to concatenate",
        "how to redirect", "how to pipe",
        "how to combine", "how to merge files",
        "how to split", "how to cut",
        "how to compress", "how to archive",
        "how to monitor", "how to track",
        "how to kill", "how to stop",
        "how to restart", "how to reload",
        "how to check disk", "how to check memory", "how to check cpu",
        "how to check process", "how to check port",
        "how to find file", "how to find process",
        "how to change permission", "how to set permission",
        "how to schedule", "how to automate",
        "how to debug script", "how to trace",
        "how to loop in", "how to iterate",
        "how to pass argument", "how to read argument",
        "how to use sed", "how to use awk", "how to use grep",
        "how to use xargs", "how to use find",
        "how to use cut", "how to use sort", "how to use uniq",
        "how to use tee", "how to use tr",
        "why", "when would", "tell me", "what will", "what if",
        "do we need", "can you", "is it", "those are", "status code",
        "time complexity", "send", "chat box", "chat book",
        "all the available", "available playbooks", "list the",
    ]
    if any(lower.startswith(t) for t in explanation_triggers):
        return False
    # Also reject if it's a conversational/follow-up question
    if any(p in lower for p in ["send this", "send me", "in the chat", "chat box", "chat book",
                                  "time complexity", "those are", "status code",
                                  "do we need", "is it the", "what will be"]):
        return False

    # Only trigger for explicit "write code/program/function" requests
    explicit_code_phrases = [
        "write code", "write a code", "write the code for",
        "write a function", "write a program", "write a script",
        "write a method", "write a class", "write a generator",
        "write script", "write a query", "code for decorator",
        "code for palindrome", "code for fibonacci",
        "simple code for", "write simple code",
        "define a class", "define a function", "define a method",
        "define a generator", "define class", "define function",
        "create a class", "create a function", "create a method",
        "implement a function", "implement a class", "implement a method",
        "implement a linked list", "implement a stack", "implement a queue",
        "implement a tree", "implement a binary search", "implement a sort",
        "implement a graph",
        "use a list comprehension", "use list comprehension",
        "yaml script", "ansible playbook", "write ansible", "write an ansible",
        "ansible role", "ansible task", "playbook for", "playbook to",
        "terraform script", "terraform plan", "terraform apply",
        "groovy script", "jenkinsfile", "dockerfile", "docker-compose",
        "sql query", "write a query", "write query", "write sql",
        "find duplicate", "second highest", "find the duplicate",
        "linux command", "shell command", "bash script",
        # Python construct examples
        "write a decorator", "write a closure", "write a generator",
        "write a context manager", "write context manager",
        "decorator example", "closure example", "generator example",
        "write an example of", "write example of",
        "give an example of", "give me an example of", "give example of",
        "with example", "with an example", "and give an example",
        "and write an example", "with code example", "example code for",
        "fibonacci with memoization", "fibonacci using memoization",
        "fibonacci memoization", "fibonacci dynamic programming",
        # Infra / script requests — "write a playbook/pipeline/manifest/..."
        "write a playbook", "write an ansible", "write a terraform",
        "write a dockerfile", "write a jenkinsfile", "write a pipeline",
        "write a bash", "write a shell", "write a yaml", "write a manifest",
        "write a cronjob", "write a cron job", "write a helm",
        "write a kubernetes", "write a k8s", "write a compose",
        "create a playbook", "create a dockerfile", "create a jenkinsfile",
        "create a pipeline", "create a terraform", "create a yaml",
        "ansible playbook for", "terraform configuration",
        "terraform config for", "playbook to install", "playbook to deploy",
        "playbook to configure", "playbook to set up", "playbook to setup",
        # "playbook for [action]" — write verb may have been swallowed by STT
        "playbook for installing", "playbook for creating", "playbook for deploying",
        "playbook for configuring", "playbook for setting up", "playbook for setup",
        "playbook for launching", "playbook for provisioning",
        # Java code requests
        "write a java", "write java", "java program", "java code for",
        "java method", "java class for", "implement in java",
        "implement stack in java", "implement queue in java", "implement linkedlist in java",
        "spring boot controller", "spring boot rest", "spring boot application",
        "hibernate mapping", "jpa repository", "write a spring",
        # JavaScript code requests
        "write a javascript", "write javascript", "js code for", "javascript function",
        "write a node", "express route", "express middleware", "react component",
        "write a react", "write a promise", "write async function",
        "javascript class", "write a hook", "usestate example", "useeffect example",
        # HTML/CSS code requests
        "write html", "html code for", "html form", "html template",
        "css for", "write css", "css class for", "style for",
        "flexbox layout", "grid layout", "responsive layout",
        # SQL code requests
        "write a sql", "sql for", "query to find", "query to get",
        "select query", "write select", "postgres query", "postgresql query",
        "write a trigger", "trigger to ", "trigger for ",
        "write a stored procedure", "write a function in sql",
        "write a cte", "write a view", "write a migration",
        # Django/Flask code requests
        "django model for", "write a model", "write a view", "write a serializer",
        "write a middleware", "write a signal", "django form for",
        "flask route for", "write a blueprint", "write a decorator for flask",
        # Shell/Linux code requests
        "write a bash", "bash script for", "shell script for", "shell one liner",
        "linux command for", "awk command", "sed command", "find command to",
        "write a cron", "cron expression for",
    ]
    if any(p in lower for p in explicit_code_phrases):
        return True

    # Infra/code script pattern across all languages
    if re.search(
        r'\b(write|create|generate|give|implement)\b.*\b'
        r'(playbook|pipeline|manifest|yaml|jenkinsfile|dockerfile|terraform|ansible|'
        r'class|method|function|query|trigger|procedure|migration|component|hook|route|blueprint|script)\b',
        lower
    ):
        return True

    # Infra code pattern: "[anything] playbook for [verb]ing" (write verb swallowed by STT)
    if re.search(r'\bplaybook\s+for\s+\w+ing\b', lower):
        return True

    # "script for creating/deploying/..." — write verb swallowed by tiny.en STT
    if re.search(r'\bscript\s+for\s+(creating|deploying|launching|provisioning|installing|setting\s+up|configuring)\b', lower):
        return True

    # "YAML file/script/manifest for deploying/creating/..."
    if re.search(r'\byaml\s+(file|script|manifest|config)\s+for\s+\w+ing\b', lower):
        return True

    # "Write ... code/function/method" pattern (e.g. "Write me a decorator code")
    if re.search(r'\bwrite\b.*\b(code|function|program|script|query|method|class)\b', lower):
        return True

    # "Define ... class/function/method" pattern
    if re.search(r'\bdefine\b.*\b(class|function|method|generator)\b', lower):
        return True

    # "example" requests always expect code
    # e.g. "explain polymorphism and give an example for that"
    # e.g. "decorator for example" → converted to "Give an example of decorator"
    if re.search(r'\b(give|show|write)\b.{0,30}\bexample\b', lower):
        return True
    if re.search(r'\bexample\b.{0,20}\b(of|for)\b', lower):
        return True

    # NEW: Implicit code requests - questions that clearly expect code output
    # Pattern: "find/get/return/calculate/reverse/sort/check ... [data structure/algorithm term]"
    implicit_code_verbs = [
        "find", "get", "return", "calculate", "compute", "reverse",
        "sort", "check", "validate", "convert", "transform", "merge",
        "filter", "remove", "delete", "insert", "add", "count",
        "sum", "multiply", "divide", "swap", "rotate", "flatten",
        "group", "split", "join", "detect", "extract",
        "create", "generate", "build", "print", "initialize",
        "implement", "write a checker", "write an", "write a",
        # NOTE: "search" removed — too generic (grep/Linux questions trigger it)
    ]

    # Algorithm/data structure terms that indicate coding
    coding_context_terms = [
        "anagram", "palindrome", "fibonacci", "factorial", "prime",
        "even", "odd", "duplicate", "unique", "missing", "largest",
        "smallest", "maximum", "minimum", "average", "median",
        # NOTE: "string" removed — too generic (causes "matching string in file" → Python)
        "list", "array", "dict", "dictionary", "set",
        "tree", "linked list", "stack", "queue", "heap", "graph",
        "matrix", "binary", "hash", "sorted", "unsorted",
        "ascending", "descending", "recursive", "iterative",
        "context manager", "memoization", "decorator", "closure",
        "generator", "iterator", "comprehension", "lambda"
    ]
    
    # Check if question has implicit code pattern: verb + coding term
    has_code_verb = any(f"{verb} " in lower or lower.startswith(verb) for verb in implicit_code_verbs)
    has_coding_term = any(term in lower for term in coding_context_terms)
    
    if has_code_verb and has_coding_term:
        return True
    
    # Pattern: "by passing [data structure]" - common in coding questions
    if re.search(r'\bby passing\b.*(list|array|string|dict)', lower):
        return True
    
    # Pattern: variable assignment in question (e.g., "str = ['eat', 'cat']")
    if re.search(r'\b\w+\s*=\s*[\[\{"\']', lower):
        return True

    return False



if __name__ == "__main__":
    tests = [
        ("What is a class in Python?", True),
        ("Explain decorators", True),
        ("Difference between list and tuple", True),
        ("How does garbage collection work?", True),
        ("Tell me about yourself", True),
        ("What is, What is, What is, What is", False),
        ("Okay", False),
        ("Can you hear me?", False),
        ("the", False),
        ("What is the", False),
        # YouTube detection
        ("In this video we will learn about Python decorators", False),
        ("Subscribe to my channel for more tutorials", False),
        ("Let's get started with today's tutorial", False),
        ("Hey guys welcome to my Python course", False),
        ("Don't forget to like and subscribe", False),
        # Vague pronoun-only questions
        ("How do you implement it?", False),
        ("Can you explain that?", False),
        ("What does it do?", False),
        # STT correction: "A, C, D" -> "CI/CD"
        ("What is a, c, d?", True),
        ("What is A C D and how do you implement it in your project?", True),
    ]

    print("=" * 50)
    print("QUESTION VALIDATOR TEST")
    print("=" * 50)

    passed = 0
    for text, expected in tests:
        is_valid, cleaned, reason = validate_question(text)
        status = "PASS" if is_valid == expected else "FAIL"
        if is_valid == expected:
            passed += 1
        print(f"{status} '{text[:50]}' -> valid={is_valid} (expected={expected})")
        if reason:
            print(f"   Reason: {reason}")

    print(f"\n{passed}/{len(tests)} tests passed")
