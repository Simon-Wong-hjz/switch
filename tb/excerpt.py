"""最新回复摘录：隐藏客户端元数据尾注，再截断正文。"""

LIMIT = 200  # 原文字符数，包含换行和 Markdown 记号


def display_text(text):
    """Hide a complete, standalone Codex memory footer, not quoted examples.

    Source records and full-reply identity/copying keep the original text.
    """
    text = text or ''
    opening, closing = '<oai-mem-citation>', '</oai-mem-citation>'
    start = text.rfind(opening)
    if start < 0 or (start and text[start - 1] not in '\r\n'):
        return text
    end = text.find(closing, start + len(opening))
    if end < 0 or text[end + len(closing):].strip():
        return text
    return text[:start].rstrip('\r\n')


def excerpt(text, limit=LIMIT):
    text = display_text(text)
    return text if len(text) <= limit else text[:limit] + '…'
