import json
import os
import sys

import requests


BASE_URL = "https://api.ozwearugg.net/rest/s1/openapi"

TOKEN_URL = f"{BASE_URL}/token"
PRODUCTS_URL = f"{BASE_URL}/products"

API_KEY = os.environ.get("OZWEAR_API_KEY")
API_SECRET = os.environ.get("OZWEAR_API_SECRET")


def get_api_token():
    """API Key와 Secret으로 임시 API Token을 발급받습니다."""

    if not API_KEY:
        raise RuntimeError("OZWEAR_API_KEY가 설정되지 않았습니다.")

    if not API_SECRET:
        raise RuntimeError("OZWEAR_API_SECRET이 설정되지 않았습니다.")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    payload = {
        "key": API_KEY,
        "secret": API_SECRET,
    }

    print("OZWear API Token 요청 중...")

    response = requests.post(
        TOKEN_URL,
        headers=headers,
        json=payload,
        timeout=60,
    )

    print(f"Token API status: {response.status_code}")

    if response.status_code < 200 or response.status_code >= 300:
        print(f"Token API response: {response.text[:500]}")
        response.raise_for_status()

    try:
        body = response.json()

    except ValueError as error:
        raise RuntimeError(
            "Token API 응답이 JSON 형식이 아닙니다."
        ) from error

    data = body.get("data", {})

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Token API의 data 형식이 예상과 다릅니다: {body}"
        )

    token = data.get("token")
    expired_time = data.get("expiredTime")

    if not token:
        raise RuntimeError(
            f"Token API 응답에서 token을 찾지 못했습니다: {body}"
        )

    print("OZWear API Token 발급 성공")

    if expired_time:
        print(f"Token expiry: {expired_time}")

    # 보안을 위해 Token 값 자체는 출력하지 않습니다.
    return token


def fetch_products(token):
    """발급받은 Token으로 OZWear 상품 데이터를 가져옵니다."""

    headers = {
        "Accept": "application/json",
        "api_key": token,
    }

    print("OZWear 상품 데이터 요청 중...")

    response = requests.get(
        PRODUCTS_URL,
        headers=headers,
        timeout=120,
    )

    print(f"Products API status: {response.status_code}")
    print(
        "Content-Type: "
        f"{response.headers.get('Content-Type', 'Unknown')}"
    )

    if response.status_code < 200 or response.status_code >= 300:
        print(f"Products API response: {response.text[:500]}")
        response.raise_for_status()

    try:
        products = response.json()

    except ValueError as error:
        raise RuntimeError(
            "Products API 응답이 JSON 형식이 아닙니다."
        ) from error

    with open(
        "ozwear_products.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            products,
            file,
            ensure_ascii=False,
            indent=2,
        )

    if isinstance(products, list):
        print(f"상품 데이터 {len(products)}건 수집 완료")

    elif isinstance(products, dict):
        print(
            "상품 API 최상위 항목: "
            f"{list(products.keys())[:20]}"
        )

        for key in ("products", "data", "items", "result"):
            value = products.get(key)

            if isinstance(value, list):
                print(f"'{key}' 데이터: {len(value)}건")
                break

    print("ozwear_products.json 저장 완료")


def main():
    token = get_api_token()
    fetch_products(token)


if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print("")
        print(f"OZWear API 테스트 실패: {error}")
        sys.exit(1)
