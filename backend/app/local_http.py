"""只信任规范的回环Host；原始Host和代理携带的Host都必须通过。"""

import re

LOCAL_HOST = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?", re.I)


def is_local_host(value: str | None) -> bool:
    match = LOCAL_HOST.fullmatch(value or "")
    if match is None:
        return False
    port = match.group(1)
    return port is None or 1 <= int(port) <= 65535
