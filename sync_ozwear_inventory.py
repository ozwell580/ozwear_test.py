"""
오즈웨어(OZWEAR UGG) 상품의 재고를 오즈웨어 API에서 가져와 Shopify에 자동 반영합니다.
오즈웨어 SKU는 API가 주는 값 그대로 Shopify에 등록되어 있으므로,
색상/사이즈로 추측하지 않고 SKU를 그대로 사용해 정확히 매칭합니다.

ozwear_test.py 저장소에 넣고 GitHub Actions로 정기 실행하세요.

필요한 GitHub Secrets:
    OZWEAR_API_KEY, OZWEAR_API_SECRET
    SHOPIFY_STORE, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET
"""

import os
import time
import requests

# ================= Configuration =================
OZWEAR_BASE_URL = "https://api.ozwearugg.net/rest/s1/openapi"
OZWEAR_API_KEY = os.environ.get("OZWEAR_API_KEY")
OZWEAR_API_SECRET = os.environ.get("OZWEAR_API_SECRET")

SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE")
SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET")

TARGET_VENDOR = "OZWEAR UGG"
STOCK_BATCH_SIZE = 50


# ================= Shopify Helpers =================
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
    res = requests.post(url, json=payload, timeout=15)
    if res.status_code == 200:
        data = res.json()
        print(f"-> Shopify 토큰 발급 성공 (범위: {data.get('scope')})")
        return data.get("access_token")
    print(f"❌ Shopify 토큰 발급 실패: {res.status_code} - {res.text}")
    return None


def get_shopify_location_id(shopify_headers, store_domain):
    url = f"https://{store_domain}/admin/api/2024-01/locations.json"
    res = requests.get(url, headers=shopify_headers)
    if res.status_code == 200:
        locations = res.json().get("locations", [])
        if locations:
            loc_id = locations[0]["id"]
            print(f"-> Location ID 조회 성공: {loc_id}")
            return loc_id
    print(f"[Location API 에러]: {res.text}")
    return None


def get_ozwear_shopify_variants(shopify_headers, store_domain, vendor):
    """지정 벤더 상품들의 SKU -> inventory_item_id 매핑을 가져옵니다."""
    sku_map = {}
    url = (
        f"https://{store_domain}/admin/api/2024-01/products.json"
        f"?limit=250&vendor={requests.utils.quote(vendor)}"
    )

    while url:
        res = requests.get(url, headers=shopify_headers)
        if res.status_code != 200:
            print(f"[Product API 에러]: {res.status_code} - {res.text}")
            break

        products = res.json().get("products", [])
        for p in products:
            for v in p.get("variants", []):
                sku = str(v.get("sku", "")).strip()
                if sku:
                    sku_map[sku] = v.get("inventory_item_id")

        link_header = res.headers.get("Link")
        url = None
        if link_header:
            for link in link_header.split(","):
                if 'rel="next"' in link:
                    url = link.split(";")[0].strip("<> ")

    return sku_map


def set_shopify_inventory(inventory_item_id, location_id, quantity, shopify_headers, store_domain):
    url = f"https://{store_domain}/admin/api/2024-01/inventory_levels/set.json"
    payload = {
        "location_id": location_id,
        "inventory_item_id": inventory_item_id,
        "available": quantity
    }
    res = requests.post(url, headers=shopify_headers, json=payload)
    return res.status_code == 200


# ================= Ozwear Helpers =================
def get_ozwear_token():
    resp = requests.post(
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
        resp = requests.post(
            f"{OZWEAR_BASE_URL}/products/stock",
            headers=headers,
            json={"skus": batch, "productIds": []},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("data", {}).get("list", [])

        for item in items:
            sku = str(item.get("sku", "")).strip()
            if sku:
                stock_map[sku] = item.get("stock", 0)

        print(f"재고 조회 {min(i + STOCK_BATCH_SIZE, len(skus))}/{len(skus)}")
        time.sleep(0.3)

    return stock_map


# ================= Main =================
def sync_ozwear_inventory():
    access_token = get_shopify_access_token()
    if not access_token:
        return

    shopify_headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json"
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

    updated_count = 0
    not_found = []

    for sku, inventory_item_id in sku_map.items():
        if sku not in stock_map:
            not_found.append(sku)
            continue

        qty = int(stock_map[sku] or 0)
        if set_shopify_inventory(inventory_item_id, location_id, qty, shopify_headers, store_domain):
            updated_count += 1

    print(f"\n완료: {updated_count}개 SKU 재고 갱신")
    if not_found:
        print(f"오즈웨어 API에서 못 찾은 SKU({len(not_found)}개) 예시: {not_found[:10]}")


if __name__ == "__main__":
    sync_ozwear_inventory()
