import json
import os
import sys
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen


def call(url, params):
    query = urlencode(params)
    req = Request(url + "?" + query, headers={"User-Agent": "solacheck-data-probe/1.0"})
    with urlopen(req, timeout=60) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        text = body.decode(charset, errors="replace")
        return response.status, response.headers.get("Content-Type", ""), text


def describe(name, result):
    status, content_type, text = result
    print(f"{name}: HTTP={status} CONTENT_TYPE={content_type} BYTES={len(text.encode('utf-8'))}")
    try:
        obj = json.loads(text)
    except Exception:
        print("  NOT_JSON_HEAD", repr(text[:500]))
        return
    if isinstance(obj, dict):
        print("  TOP_KEYS", list(obj.keys()))
        for key in ("response", "body", "header", "body", "data", "items", "result"):
            value = obj.get(key)
            if isinstance(value, dict):
                print(" ", key, "KEYS", list(value.keys()))
                for subkey in ("totalCount", "numOfRows", "pageNo", "resultCode", "resultMsg", "items", "data"):
                    if subkey in value:
                        sub = value[subkey]
                        if isinstance(sub, list):
                            print("  ", key, subkey, "LEN", len(sub), "SAMPLE_KEYS", list(sub[0].keys()) if sub and isinstance(sub[0], dict) else None)
                        else:
                            print("  ", key, subkey, repr(sub)[:500])
            elif isinstance(value, list):
                print("  ", key, "LEN", len(value), "SAMPLE_KEYS", list(value[0].keys()) if value and isinstance(value[0], dict) else None)
    else:
        print("  JSON_TYPE", type(obj).__name__)


solar_raw = os.environ.get("SOLAR_KEY_RAW")
kepco_key = os.environ.get("KEPCO_KEY")
if not solar_raw or not kepco_key:
    raise SystemExit("Missing task-specific API key environment variables")

solar_url = "https://api.data.go.kr/openapi/tn_pubr_public_solar_gen_flct_api"
kepco_url = "https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do"

try:
    describe(
        "SOLAR",
        call(
            solar_url,
            {
                "serviceKey": unquote(solar_raw),
                "pageNo": "1",
                "numOfRows": "5",
                "type": "json",
            },
        ),
    )
except Exception as exc:
    print("SOLAR_ERROR", type(exc).__name__, str(exc)[:500])

try:
    describe(
        "KEPCO",
        call(kepco_url, {"apiKey": kepco_key, "returnType": "json"}),
    )
except Exception as exc:
    print("KEPCO_ERROR", type(exc).__name__, str(exc)[:500])
