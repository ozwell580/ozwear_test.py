import csv
import json
import os
import sys
import time

import requests
import urllib3


TOKEN_URL = "https://api.ozwearugg.net/rest/s1/openapi/token"
PRODUCTS_URL = "https://api.ozwearugg.net/rest/s1/openapi/products"
STOCK_URL = "https://api.ozwearugg.net/rest/s1/openapi/products/stock"

API_KEY = os.environ.get("OZWEAR_API_KEY")
API_SECRET = os.environ.get("OZWEAR_API_SECRET")

# 오즈웨어 서버 인증서 문제 때문에 테스트에서만 SSL 검증을 끕니다.
VERIFY_SSL = os.environ.get(
    "OZWEAR_VERIFY_SSL", "false"
).lower() == "true"

if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def post_json(url, headers=None, body=None):
    """OZWear API에 POST 요청을 보냅니다."""

    response = requests.post(
        url,
        headers=headers or {},
        json=body or {},
        timeout=60,
        verify=VERIFY_SSL,
    )

    print(f"POST {url}")
    print(f"HTTP status: {response.status_code}")

    if not response.ok:
        print(f"서버 응답: {response.text[:1000]}")
        response.raise_for_status()

    try:
        return response.json()

    except ValueError as error:
        raise RuntimeError(
            f"JSON 응답이 아닙니다: {response.text[:500]}"
        ) from error


def get_token():
    """API Key와 Secret으로 토큰을 발급받습니다."""

    print("\n1. OZWear API 토큰 요청 중...")

    response = post_json(
        TOKEN_URL,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        body={
            "key": API_KEY,
            "secret": API_SECRET,
        },
    )

    token = response.get("data", {}).get("token")

    if not token:
        raise RuntimeError(
            f"토큰이 응답에 없습니다: {json.dumps(response, ensure_ascii=False)}"
        )

    print("토큰 발급 성공")
    return token


def get_products(token):
    """상품 API에서 테스트용 상품 목록을 가져옵니다."""

    print("\n2. OZWear 상품 목록 요청 중...")

    response = post_json(
        PRODUCTS_URL,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "api_key": token,
        },
        body={},
    )

    products = response.get("data", {}).get("list", [])
    total = response.get("data", {}).get("total", 0)

    if not isinstance(products, list):
        raise RuntimeError("상품 목록 형식이 올바르지 않습니다.")

    print(f"전체 상품 옵션 수: {total}")
    print(f"이번 테스트 상품 옵션 수: {len(products)}")

    with open("ozwear_products.json", "w", encoding="utf-8") as file:
        json.dump(response, file, ensure_ascii=False, indent=2)

    return products


def split_batches(values, batch_size=50):
    """SKU를 최대 50개씩 나눕니다."""

    for start in range(0, len(values), batch_size):
        yield values[start:start + batch_size]


def get_stocks(token, products):
    """상품 SKU를 50개씩 나누어 재고를 조회합니다."""

    skus = []

    for product in products:
        sku = str(product.get("sku", "")).strip()

        if sku and sku not in skus:
            skus.append(sku)

    if not skus:
        raise RuntimeError("상품 목록에서 SKU를 찾지 못했습니다.")

    print(f"\n3. 총 {len(skus)}개 SKU 재고 조회 시작...")

    all_stocks = []

    for batch_number, sku_batch in enumerate(
        split_batches(skus, 50),
        start=1,
    ):
        print(
            f"재고 요청 {batch_number}: "
            f"{len(sku_batch)}개 SKU"
        )

        response = post_json(
            STOCK_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "api_key": token,
            },
            body={
                "skus": sku_batch,
                "productIds": [],
            },
        )

        stock_list = response.get("data", {}).get("list", [])

        if not isinstance(stock_list, list):
            raise RuntimeError(
                f"재고 응답 형식이 올바르지 않습니다: {response}"
            )

        all_stocks.extend(stock_list)

        # API 서버에 무리가 가지 않도록 요청 사이에 잠시 대기
        time.sleep(0.5)

    print(f"재고 조회 완료: {len(all_stocks)}건")

    with open("ozwear_stocks.json", "w", encoding="utf-8") as file:
        json.dump(
            {
                "data": {
                    "list": all_stocks,
                    "count": len(all_stocks),
                }
            },
            file,
            ensure_ascii=False,
            indent=2,
        )

    return all_stocks


def create_inventory_csv(products, stocks):
    """상품정보와 재고정보를 SKU 기준으로 합쳐 CSV로 저장합니다."""

    stock_map = {}

    for stock_item in stocks:
        sku = str(stock_item.get("sku", "")).strip()

        if sku:
            stock_map[sku] = stock_item

    rows = []

    for product in products:
        sku = str(product.get("sku", "")).strip()
        stock_item = stock_map.get(sku, {})

        try:
            stock_quantity = int(stock_item.get("stock", 0) or 0)

        except (TypeError, ValueError):
            stock_quantity = 0

        rows.append(
            {
                "productId": product.get("productId", ""),
                "code": product.get("code", ""),
                "sku": sku,
                "name": product.get("name", ""),
                "color": product.get("color", ""),
                "auSize": product.get("auSize", ""),
                "euSize": product.get("euSize", ""),
                "stock": stock_quantity,
                "status": stock_item.get("status", ""),
                "imageUrl": product.get("imageUrl", ""),
            }
        )

    fieldnames = [
        "productId",
        "code",
        "sku",
        "name",
        "color",
        "auSize",
        "euSize",
        "stock",
        "status",
        "imageUrl",
    ]

    with open(
        "ozwear_inventory.csv",
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    matched = sum(1 for row in rows if row["sku"] in stock_map)

    print("\n4. 재고 CSV 생성 완료")
    print(f"상품 수: {len(rows)}")
    print(f"재고 API와 일치한 SKU: {matched}")
    print("저장 파일: ozwear_inventory.csv")


def main():
    if not API_KEY:
        raise RuntimeError("OZWEAR_API_KEY Secret이 없습니다.")

    if not API_SECRET:
        raise RuntimeError("OZWEAR_API_SECRET Secret이 없습니다.")

    token = get_token()
    products = get_products(token)
    stocks = get_stocks(token, products)
    create_inventory_csv(products, stocks)

    print("\nOZWear 상품 및 재고 API 테스트 성공")


if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print(f"\nOZWear API 테스트 실패: {error}")
        sys.exit(1)
