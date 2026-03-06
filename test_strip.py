def _strip_lang_tags(text: str, in_tag: list[bool]) -> str:
    result = []
    i = 0
    while i < len(text):
        if in_tag[0]:
            end = text.find(">", i)
            if end == -1:
                break
            in_tag[0] = False
            i = end + 1
            continue

        lang_start = text.find("<lang", i)
        close_start = text.find("</lang>", i)
        candidates = [(pos, typ) for pos, typ in [(lang_start, "open"), (close_start, "close")] if pos != -1]
        if not candidates:
            result.append(text[i:])
            break

        next_pos, typ = min(candidates, key=lambda x: x[0])
        result.append(text[i:next_pos])

        if typ == "close":
            i = next_pos + len("</lang>")
            continue

        end = text.find(">", next_pos)
        if end == -1:
            in_tag[0] = True
            break
        i = end + 1

    return "".join(result)

chunks = ['<lang ', 'primary="zh-CN', '">以下是']
state = [False]
res = ""
for c in chunks:
    res += _strip_lang_tags(c, state)
print(f"Test 1: {res}")

chunks2 = ['<lang ', 'primary="zh-', 'CN">以下是']
state2 = [False]
res2 = ""
for c in chunks2:
    res2 += _strip_lang_tags(c, state2)
print(f"Test 2: {res2}")
