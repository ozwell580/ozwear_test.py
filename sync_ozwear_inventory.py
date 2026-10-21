"""
오즈웨어(OZWEAR UGG) 상품의 재고를 오즈웨어 API에서 가져와 Shopify에 자동 반영합니다.
오즈웨어 SKU는 API가 주는 값 그대로 Shopify에 등록되어 있으므로,
색상/사이즈로 추측하지 않고 SKU를 그대로 사용해 정확히 매칭합니다.

ozwear_test.py 저장소에 넣고 GitHub Actions로 정기 실행하세요.

필요한 GitHub Secrets:
    OZWEAR_API_KEY, OZWEAR_API_SECRET
    SHOPIFY_STORE, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET

[개선 사항]
- requests.Session 으로 연결 재사용 (SSL 연결 끊김 에러 방지)
- 연결 에러 / 429 / 5xx 발생 시 자동 재시도
- 현재 Shopify 재고를 먼저 조회해서 수량이 바뀐 SKU만 업데이트 (요청 수 대폭 감소)
- SKU 하나가 실패해도 전체가 멈추지 않고 계속 진행, 실패 목록은 마지막에 출력
- Shopify API 요청 속도 제한(초당 2회) 준수
"""

import os
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ================= Configuration =================
OZWEAR_BASE_URL = "https://api.ozwearugg.net/rest/s1/openapi"
OZWEAR_API_KEY = os.environ.get("OZWEAR_API_KEY")
OZWEAR_API_SECRET = os.environ.get("OZWEAR_API_SECRET")

SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE")
SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET")

SHOPIFY_API_VERSION = "2026-01"

TARGET_VENDOR = "OZWEAR UGG"
STOCK_BATCH_SIZE = 50          # 오즈웨어 재고 조회 묶음 크기
LEVELS_BATCH_SIZE = 50         # Shopify 현재 재고 조회 묶음 크기
SHOPIFY_WRITE_DELAY = 0.55     # Shopify 재고 업데이트 사이 대기(초) - 초당 2회 제한 대응
MANUAL_RETRIES = 3             # 세션 재시도 후에도 실패할 때 추가 재시도 횟수


# ================= Session (연결 재사용 + 자동 재시도) =================
def build_session():
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.5,                 # 1.5s, 3s, 6s, 12s ... 대기 후 재시도
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=None,               # POST 포함 모든 메서드 재시도
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()


def request_with_retry(method, url, **kwargs):
    """세션 자동 재시도 후에도 SSL/연결 에러가 나면 잠깐 쉬고 다시 시도합니다."""
    kwargs.setdefault("timeout", 60)
    last_error = None
    for attempt in range(1, MANUAL_RETRIES + 1):
        try:
            return SESSION.request(method, url, **kwargs)
        except requests.exceptions.RequestException as e:
            last_error = e
            wait = 10 * attempt
            print(f"   ⚠️ 요청 에러 ({attempt}/{MANUAL_RETRIES}), {wait}초 후 재시도: {e.__class__.__name__}")
            time.sleep(wait)
    raise last_error


# ================= Shopify Helpers =================
def shopify_url(store_domain, path):
    return f"https://{store_domain}/admin/api/{SHOPIFY_API_VERSION}/{path}"


def get_shopify_access_token():
    if not SHOPIFY_STORE or not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
        print("❌ Shopify 관련 환경변수가 없습니다.")
        return None

    store_domain = SHOPIFY_STORE.replace("https://", "").strip("/")
    url = f"https://{store_domain}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_CLIENT_ID,
        "client_secret": SHOPIFY_CLIENT_SECRET,
        "grant_type": "client_credentials",
    }
    res = request_with_retry("POST", url, json=payload, timeout=15)
    if res.status_code == 200:
        data = res.json()
        print(f"-> Shopify 토큰 발급 성공 (범위: {data.get('scope')})")
        return data.get("access_token")
    print(f"❌ Shopify 토큰 발급 실패: {res.status_code} - {res.text}")
    return None


def get_shopify_location_id(shopify_headers, store_domain):
    res = request_with_retry("GET", shopify_url(store_domain, "locations.json"), headers=shopify_headers)
    if res.status_code == 200:
        locations = res.json().get("locations", [])
        if locations:
            loc_id = locations[0]["id"]
            print(f"-> Location ID 조회 성공: {loc_id}")
            return loc_id
    print(f"[Location API 에러]: {res.status_code} - {res.text}")
    return None


def get_ozwear_shopify_variants(shopify_headers, store_domain, vendor):
    """지정 벤더 상품들의 SKU -> inventory_item_id 매핑을 가져옵니다."""
    sku_map = {}
    url = shopify_url(
        store_domain,
        f"products.json?limit=250&fields=id,variants&vendor={requests.utils.quote(vendor)}",
    )

    while url:
        res = request_with_retry("GET", url, headers=shopify_headers)
        if res.status_code != 200:
            print(f"[Product API 에러]: {res.status_code} - {res.text}")
            break

        products = res.json().get("products", [])
        for p in products:
            for v in p.get("variants", []):
                sku = str(v.get("sku") or "").strip()
                if sku:
                    sku_map[sku] = v.get("inventory_item_id")

        link_header = res.headers.get("Link")
        url = None
        if link_header:
            for link in link_header.split(","):
                if 'rel="next"' in link:
                    url = link.split(";")[0].strip("<> ")

        time.sleep(0.5)

    return sku_map


def get_current_shopify_levels(inventory_item_ids, location_id, shopify_headers, store_domain):
    """현재 Shopify 재고를 inventory_item_id -> available 형태로 가져옵니다."""
    levels = {}
    ids = [i for i in inventory_item_ids if i]

    for i in range(0, len(ids), LEVELS_BATCH_SIZE):
        batch = ids[i:i + LEVELS_BATCH_SIZE]
        url = shopify_url(
            store_domain,
            f"inventory_levels.json?limit=250&location_ids={location_id}"
            f"&inventory_item_ids={','.join(str(x) for x in batch)}",
        )
        try:
            res = request_with_retry("GET", url, headers=shopify_headers)
            if res.status_code == 200:
                for lv in res.json().get("inventory_levels", []):
                    levels[lv["inventory_item_id"]] = lv.get("available")
            else:
                print(f"   [현재 재고 조회 에러]: {res.status_code} - {res.text[:200]}")
        except requests.exceptions.RequestException as e:
            print(f"   [현재 재고 조회 실패]: {e.__class__.__name__}")

        done = min(i + LEVELS_BATCH_SIZE, len(ids))
        if done % 1000 < LEVELS_BATCH_SIZE or done == len(ids):
            print(f"현재 Shopify 재고 조회 {done}/{len(ids)}")
        time.sleep(0.5)

    return levels


def set_shopify_inventory(inventory_item_id, location_id, quantity, shopify_headers, store_domain):
    payload = {
        "location_id": location_id,
        "inventory_item_id": inventory_item_id,
        "available": quantity,
    }
    try:
        res = request_with_retry(
            "POST",
            shopify_url(store_domain, "inventory_levels/set.json"),
            headers=shopify_headers,
            json=payload,
        )
    except requests.exceptions.RequestException as e:
        print(f"   ❌ 재고 업데이트 연결 실패 (item {inventory_item_id}): {e.__class__.__name__}")
        return False

    if res.status_code == 200:
        return True
    print(f"   ❌ 재고 업데이트 실패 (item {inventory_item_id}): {res.status_code} - {res.text[:200]}")
    return False


# ================= Ozwear Helpers =================
def get_ozwear_token():
    resp = request_with_retry(
        "POST",
        f"{OZWEAR_BASE_URL}/token",
        json={"key": OZWEAR_API_KEY, "secret": OZWEAR_API_SECRET},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errorCode") not in (0, "0", None):
        raise RuntimeError(f"오즈웨어 토큰 발급 실패: {payload}")
    print("-> 오즈웨어 토큰 발급 성공")
    return payload["data"]["token"]


def fetch_ozwear_stock(token, skus):
    headers = {"api_key": token, "Content-Type": "application/json"}
    stock_map = {}

    for i in range(0, len(skus), STOCK_BATCH_SIZE):
        batch = skus[i:i + STOCK_BATCH_SIZE]
        try:
            resp = request_with_retry(
                "POST",
                f"{OZWEAR_BASE_URL}/products/stock",
                headers=headers,
                json={"skus": batch, "productIds": []},
                timeout=60,
            )
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("list", [])
            for item in items:
                sku = str(item.get("sku") or "").strip()
                if sku:
                    stock_map[sku] = item.get("stock", 0)
        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"   ⚠️ 오즈웨어 재고 조회 실패 (배치 {i}~{i + len(batch)}): {e.__class__.__name__}")

        done = min(i + STOCK_BATCH_SIZE, len(skus))
        if done % 500 < STOCK_BATCH_SIZE or done == len(skus):
            print(f"재고 조회 {done}/{len(skus)}")
        time.sleep(0.3)

    return stock_map


# ================= Main =================
def sync_ozwear_inventory():
    access_token = get_shopify_access_token()
    if not access_token:
        return

    shopify_headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }
    store_domain = SHOPIFY_STORE.replace("https://", "").strip("/")

    location_id = get_shopify_location_id(shopify_headers, store_domain)
    if not location_id:
        return

    print(f"'{TARGET_VENDOR}' 상품 SKU 수집 중...")
    sku_map = get_ozwear_shopify_variants(shopify_headers, store_domain, TARGET_VENDOR)
    print(f"-> {len(sku_map)}개 SKU 수집 완료")

    if not sku_map:
        print("오즈웨어 상품이 없습니다.")
        return

    if not OZWEAR_API_KEY or not OZWEAR_API_SECRET:
        print("❌ 오즈웨어 API 키/시크릿이 없습니다.")
        return

    token = get_ozwear_token()
    all_skus = list(sku_map.keys())
    print(f"오즈웨어 API에서 재고 조회 중 (총 {len(all_skus)}개 SKU)...")
    stock_map = fetch_ozwear_stock(token, all_skus)
    print(f"-> {len(stock_map)}개 SKU 재고 데이터 수신 완료")

    print("Shopify 현재 재고 조회 중...")
    current_levels = get_current_shopify_levels(
        list(sku_map.values()), location_id, shopify_headers, store_domain
    )
    print(f"-> {len(current_levels)}개 현재 재고 확인 완료")

    # 바뀐 것만 골라내기
    to_update = []
    not_found = []
    unchanged = 0

    for sku, inventory_item_id in sku_map.items():
        if sku not in stock_map:
            not_found.append(sku)
            continue
        try:
            qty = max(int(stock_map[sku] or 0), 0)
        except (TypeError, ValueError):
            qty = 0

        if current_levels.get(inventory_item_id) == qty:
            unchanged += 1
            continue
        to_update.append((sku, inventory_item_id, qty))

    print(f"-> 변경 필요 {len(to_update)}개 / 변경 없음 {unchanged}개")

    updated_count = 0
    failed = []

    for idx, (sku, inventory_item_id, qty) in enumerate(to_update, 1):
        if set_shopify_inventory(inventory_item_id, location_id, qty, shopify_headers, store_domain):
            updated_count += 1
        else:
            failed.append(sku)

        if idx % 100 == 0 or idx == len(to_update):
            print(f"재고 업데이트 {idx}/{len(to_update)}")
        time.sleep(SHOPIFY_WRITE_DELAY)

    print(f"\n완료: {updated_count}개 SKU 재고 갱신 (변경 없음 {unchanged}개)")
    if failed:
        print(f"⚠️ 업데이트 실패 SKU({len(failed)}개) 예시: {failed[:10]}")
    if not_found:
        print(f"오즈웨어 API에서 못 찾은 SKU({len(not_found)}개) 예시: {not_found[:10]}")


if __name__ == "__main__":
    sync_ozwear_inventory()
