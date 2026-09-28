import re

from accelerator.domain import ProxyNode

COUNTRIES = (
    ("HK", "中国香港", "", r"香港|hong[ -]?kong|🇭🇰|\bhk\b"),
    ("TW", "中国台湾", "", r"台湾|台灣|taiwan|taipei|🇹🇼|\btw\b"),
    ("JP", "日本", "Tokyo", r"日本|东京|東京|japan|tokyo|🇯🇵|\bjp\b"),
    ("KR", "韩国", "", r"韩国|韓國|korea|seoul|🇰🇷|\bkr\b"),
    ("SG", "新加坡", "", r"新加坡|singapore|🇸🇬|\bsg\b"),
    ("US", "美国", "", r"美国|美國|united states|🇺🇸|\b(?:us|usa)\b"),
    ("GB", "英国", "", r"英国|英國|united kingdom|london|🇬🇧|\b(?:uk|gb)\b"),
    ("DE", "德国", "", r"德国|德國|germany|frankfurt|🇩🇪|\bde\b"),
    ("CA", "加拿大", "", r"加拿大|canada|toronto|🇨🇦|\bca\b"),
    ("AU", "澳大利亚", "", r"澳大利亚|australia|sydney|🇦🇺|\bau\b"),
)


def classify(node: ProxyNode) -> ProxyNode:
    name = node.secret.name
    for country, region, city, pattern in COUNTRIES:
        if re.search(pattern, name, re.IGNORECASE):
            node.country = country
            node.region = region
            node.city = city if re.search(r"tokyo|东京|東京", name, re.IGNORECASE) else ""
            break
    node.tags = ["普通网络"]
    if re.search(r"游戏|遊戲|\bgam(?:e|ing)\b", name, re.IGNORECASE):
        node.tags.append("游戏")
    if re.search(r"视频|netflix|youtube|stream", name, re.IGNORECASE):
        node.tags.append("视频")
    if re.search(r"备用|backup", name, re.IGNORECASE):
        node.tags.append("备用")
    return node
