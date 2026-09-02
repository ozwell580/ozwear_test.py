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

PAGE_SIZE = 50
STOCK_BATCH_SIZE = 50

# 오즈웨어 서버의 SSL 인증서 문제로 테스트에서 검증을 끕니다.
VERIFY_SSL = os.environ.get(
    "OZWEAR_VERIFY_SSL",
    "false",
).lower() == "true"

if not VERIFY_SSL:
    urllib3.disable_warnings(
        urllib3.exceptions.InsecureRequestWarning
    )


session = requests.Session()


def post_json(url, headers=None, body=None, max_retries=3):
    """POST 요청을 보내며 일시적인 오류 발생 시 재시도합니다."""

    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = session.post(
                url,
                headers=headers or {},
                json=body or {},
                timeout=90,
                verify=VERIFY_SSL,
            )

            if response.status_code == 429:
                wait_seconds = int(
                    response.headers.get("Retry-After", 5)
                )

                print(
                    f"요청 제한 발생. "
                    f"{wait_seconds}초 후 다시 시도합니다."
                )

                time.sleep(wait_seconds)
                continue

            if 500 <= response.status_code < 600:
                print(
                    f"서버 오류 {response.status_code}. "
                    f"요청 재시도 {attempt}/{max_retries}"
                )

                time.sleep(attempt * 3)
                continue

            if not response.ok:
                print(f"HTTP status: {response.status_code}")
                print(f"서버 응답: {response.text[:1000]}")
                response.raise_for_status()

            try:
                return response.json()

            except ValueError as error:
                raise RuntimeError(
                    "API 응답이 JSON 형식이 아닙니다. "
                    f"응답: {response.text[:500]}"
                ) from error

        except requests.exceptions.RequestException as error:
            last_error = error

            print(
                f"네트워크 요청 오류. "
                f"재시도 {attempt}/{max_retries}: {error}"
            )

            if attempt < max_retries:
                time.sleep(attempt * 3)

    raise RuntimeError(
        f"API 요청이 최종 실패했습니다: {last_error}"
    )


def get_token():
    """API Key와 Secret으로 인증 토큰을 발급받습니다."""

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
            "토큰이 API 응답에 없습니다. "
            f"응답: {json.dumps(response, ensure_ascii=False)}"
        )

    print("토큰 발급 성공")

    return token


def get_api_headers(token):
    """토큰 인증이 포함된 공통 헤더를 반환합니다."""

    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "api_key": token,
    }


def get_products(token):
    """상품 API의 전체 페이지를 순서대로 조회합니다."""

    print("\n2. OZWear 전체 상품 목록 요청 중...")

    headers = get_api_headers(token)

    all_products = []
    seen_skus = set()

    page = 1
    total = None

    while True:
        response = post_json(
            PRODUCTS_URL,
            headers=headers,
            body={
                "code": "",
                "page": page,
                "pageSize": PAGE_SIZE,
            },
        )

        data = response.get("data", {})
        products = data.get("list", [])

        if not isinstance(products, list):
            raise RuntimeError(
                f"상품 목록 형식이 올바르지 않습니다: {data}"
            )

        if total is None:
            total = int(data.get("total", 0) or 0)
            print(f"전체 상품 옵션 수: {total}")

        if not products:
            print(
                f"페이지 {page}에 상품이 없어 "
                "상품 조회를 종료합니다."
            )
            break

        added_count = 0

        for product in products:
            sku = str(product.get("sku", "")).strip()

            if not sku:
                print(
                    "경고: SKU가 없는 상품을 제외합니다. "
                    f"productId={product.get('productId')}"
                )
                continue

            if sku in seen_skus:
                continue

            seen_skus.add(sku)
            all_products.append(product)
            added_count += 1

        print(
            f"페이지 {page}: "
            f"{len(products)}건 수신 / "
            f"{added_count}건 추가 / "
            f"누적 {len(all_products)}건"
        )

        if total and page * PAGE_SIZE >= total:
            break

        if len(products) < PAGE_SIZE:
            break

        page += 1

        # API 서버에 과도한 요청을 보내지 않도록 잠시 대기
        time.sleep(0.3)

    if not all_products:
        raise RuntimeError("상품을 한 건도 가져오지 못했습니다.")

    result = {
        "data": {
            "total": total,
            "downloaded": len(all_products),
            "list": all_products,
        },
        "errorCode": "0",
        "message": "success",
    }

    with open(
        "ozwear_products.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            result,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"\n전체 상품 저장 완료: "
        f"{len(all_products)}건"
    )

    return all_products


def split_batches(values, batch_size):
    """목록을 지정된 개수씩 나눕니다."""

    for start in range(0, len(values), batch_size):
        yield values[start:start + batch_size]


def get_stocks(token, products):
    """모든 SKU를 50개씩 나누어 재고를 조회합니다."""

    headers = get_api_headers(token)

    skus = []
    seen_skus = set()

    for product in products:
        sku = str(product.get("sku", "")).strip()

        if not sku or sku in seen_skus:
            continue

        seen_skus.add(sku)
        skus.append(sku)

    if not skus:
        raise RuntimeError(
            "상품 목록에서 재고 조회용 SKU를 찾지 못했습니다."
        )

    batches = list(
        split_batches(skus, STOCK_BATCH_SIZE)
    )

    print("\n3. OZWear 전체 재고 조회 시작...")
    print(f"조회할 SKU: {len(skus)}개")
    print(f"재고 요청 횟수: {len(batches)}회")

    all_stocks = []
    returned_skus = set()

    for batch_number, sku_batch in enumerate(
        batches,
        start=1,
    ):
        response = post_json(
            STOCK_URL,
            headers=headers,
            body={
                "skus": sku_batch,
                "productIds": [],
            },
        )

        stock_list = (
            response.get("data", {}).get("list", [])
        )

        if not isinstance(stock_list, list):
            raise RuntimeError(
                "재고 응답 형식이 올바르지 않습니다. "
                f"응답: {response}"
            )

        for stock_item in stock_list:
            sku = str(
                stock_item.get("sku", "")
            ).strip()

            if sku:
                returned_skus.add(sku)

            all_stocks.append(stock_item)

        print(
            f"재고 요청 {batch_number}/{len(batches)}: "
            f"{len(stock_list)}건 수신 / "
            f"누적 {len(all_stocks)}건"
        )

        time.sleep(0.3)

    missing_skus = [
        sku for sku in skus
        if sku not in returned_skus
    ]

    result = {
        "data": {
            "requested": len(skus),
            "returned": len(all_stocks),
            "missingCount": len(missing_skus),
            "missingSkus": missing_skus,
            "list": all_stocks,
        },
        "errorCode": "0",
        "message": "success",
    }

    with open(
        "ozwear_stocks.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            result,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print("\n전체 재고 조회 완료")
    print(f"요청 SKU: {len(skus)}개")
    print(f"재고 응답: {len(all_stocks)}개")
    print(f"재고 응답 누락: {len(missing_skus)}개")

    return all_stocks


def parse_stock_quantity(value):
    """재고 값을 정수로 변환합니다."""

    try:
        return int(value or 0)

    except (TypeError, ValueError):
        return 0


def create_inventory_csv(products, stocks):
    """상품과 재고를 SKU 기준으로 합쳐 CSV로 저장합니다."""

    stock_map = {}

    for stock_item in stocks:
        sku = str(
            stock_item.get("sku", "")
        ).strip()

        if sku:
            stock_map[sku] = stock_item

    rows = []

    for product in products:
        sku = str(product.get("sku", "")).strip()

        matched = sku in stock_map
        stock_item = stock_map.get(sku, {})

        if matched:
            stock_quantity = parse_stock_quantity(
                stock_item.get("stock")
            )
            stock_status = stock_item.get("status", "")
        else:
            # API 응답이 누락된 SKU를 품절로 처리하지 않습니다.
            stock_quantity = ""
            stock_status = "not_returned"

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
                "status": stock_status,
                "stockMatched": "YES" if matched else "NO",
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
        "stockMatched",
        "imageUrl",
    ]

    with open(
        "ozwear_inventory.csv",
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(rows)

    matched_count = sum(
        1 for row in rows
        if row["stockMatched"] == "YES"
    )

    unmatched_count = len(rows) - matched_count

    positive_count = sum(
        1 for row in rows
        if isinstance(row["stock"], int)
        and row["stock"] > 0
    )

    out_of_stock_count = sum(
        1 for row in rows
        if row["stock"] == 0
        and row["stockMatched"] == "YES"
    )

    print("\n4. 전체 재고 CSV 생성 완료")
    print(f"전체 상품 옵션: {len(rows)}개")
    print(f"재고 매칭 성공: {matched_count}개")
    print(f"재고 응답 누락: {unmatched_count}개")
    print(f"재고 있음: {positive_count}개")
    print(f"품절: {out_of_stock_count}개")
    print("저장 파일: ozwear_inventory.csv")


def main():
    if not API_KEY:
        raise RuntimeError(
            "GitHub Secret OZWEAR_API_KEY가 없습니다."
        )

    if not API_SECRET:
        raise RuntimeError(
            "GitHub Secret OZWEAR_API_SECRET이 없습니다."
        )

    token = get_token()
    products = get_products(token)
    stocks = get_stocks(token, products)
    create_inventory_csv(products, stocks)

    print("\nOZWear 전체 상품 및 재고 API 테스트 성공")


if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print(f"\nOZWear API 테스트 실패: {error}")
        sys.exit(1)
