"""Common English nickname expansions. Bidirectional — Bob<->Robert.

Not exhaustive on purpose. The LLM tiebreaker handles anything obscure.
"""

_PAIRS: list[tuple[str, list[str]]] = [
    ("alexander", ["alex", "xander", "lex", "sasha"]),
    ("alexandra", ["alex", "alexa", "lexi", "sandra", "sasha"]),
    ("anthony", ["tony", "ant"]),
    ("benjamin", ["ben", "benny", "benji"]),
    ("catherine", ["cathy", "kate", "katie", "kat", "cat", "kit"]),
    ("christina", ["chris", "tina", "christy"]),
    ("christopher", ["chris", "topher", "kit"]),
    ("daniel", ["dan", "danny"]),
    ("david", ["dave", "davey"]),
    ("deborah", ["deb", "debbie"]),
    ("dorothy", ["dot", "dottie", "dolly"]),
    ("edward", ["ed", "eddie", "ted", "ned"]),
    ("elizabeth", ["liz", "beth", "betty", "eliza", "betsy", "lizzy"]),
    ("frances", ["fran", "frannie", "francie"]),
    ("francis", ["frank", "frankie"]),
    ("frederick", ["fred", "freddie", "rick"]),
    ("gabriel", ["gabe"]),
    ("genevieve", ["gen", "jen", "ginny"]),
    ("gerald", ["gerry", "jerry"]),
    ("gregory", ["greg"]),
    ("harold", ["hal", "harry"]),
    ("henry", ["hank", "harry", "hal"]),
    ("james", ["jim", "jimmy", "jamie"]),
    ("jennifer", ["jen", "jenny", "jenna"]),
    ("jessica", ["jess", "jessie"]),
    ("john", ["johnny", "jack", "jon"]),
    ("jonathan", ["jon", "jonny"]),
    ("joseph", ["joe", "joey"]),
    ("joshua", ["josh"]),
    ("katherine", ["kathy", "kate", "katie", "kat"]),
    ("kenneth", ["ken", "kenny"]),
    ("lawrence", ["larry", "lars"]),
    ("leonard", ["leo", "lenny"]),
    ("margaret", ["maggie", "meg", "peggy", "marge"]),
    ("matthew", ["matt", "matty"]),
    ("michael", ["mike", "mickey", "mick"]),
    ("nancy", ["nan", "nance"]),
    ("nathaniel", ["nate", "nat"]),
    ("nicholas", ["nick", "nicky"]),
    ("pamela", ["pam"]),
    ("patricia", ["pat", "patty", "trish", "tricia"]),
    ("patrick", ["pat", "paddy", "rick"]),
    ("rebecca", ["becky", "becca"]),
    ("richard", ["rick", "ricky", "dick", "rich"]),
    ("robert", ["bob", "bobby", "rob", "robbie"]),
    ("ronald", ["ron", "ronnie"]),
    ("samuel", ["sam", "sammy"]),
    ("sandra", ["sandy", "sandi"]),
    ("stephanie", ["steph", "steffi"]),
    ("stephen", ["steve", "steven"]),
    ("steven", ["steve", "stephen"]),
    ("susan", ["sue", "susie", "suzy"]),
    ("theodore", ["ted", "teddy", "theo"]),
    ("thomas", ["tom", "tommy"]),
    ("timothy", ["tim", "timmy"]),
    ("victoria", ["vicky", "tori"]),
    ("william", ["will", "bill", "billy", "willy", "liam"]),
    ("zachary", ["zach", "zack"]),
]


_INDEX: dict[str, set[str]] = {}
for canonical, nicks in _PAIRS:
    group = {canonical, *nicks}
    for token in group:
        _INDEX.setdefault(token.lower(), set()).update(group)


def equivalents(name: str) -> set[str]:
    """Return all known equivalents (including the input itself, lowercased)."""
    key = name.strip().lower()
    return _INDEX.get(key, {key})


def names_could_match(a: str, b: str) -> bool:
    """True if a and b are the same name or known nicknames of each other."""
    return bool(equivalents(a) & equivalents(b))
