import json
import os
import sys

import requests


OZWEAR_API_URL = os.environ.get(
    "OZWEAR_API_URL",
    "https://api.ozwearugg.net/rest/s1/openapi/products",
)

OZWEAR_API_KEY = os.environ.get("OZWEAR_API_KEY")
OZWEAR_API_SECRET = os.environ.get("OZWEAR_API_SECRET")


def fetch_ozwear_products():
    if not OZWEAR_API_KEY or not OZWEAR_API_SECRET:
        raise RuntimeError(
            "OZWear API Key 또는 Secret이 설정되지 않았습니다."
        )

    headers = {
        "Accept": "application/json",
        "API-Key": OZWEAR_API_KEY,
        "API-Secret": OZWEAR_API_SECRET,
    }

    response = requests.get(
        OZWEAR_API_URL,
        headers=headers,
        timeout=60,
    )

    print(f"HTTP status: {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type')}")

    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "API 응답이 JSON 형식이 아닙니다."
        ) from exc

    with open("ozwear_products.json", "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )

    if isinstance(data, list):
        print(f"상품 데이터 {len(data)}건을 가져왔습니다.")
    elif isinstance(data, dict):
        keys = list(data.keys())[:20]
        print(f"응답 최상위 항목: {keys}")

    print("ozwear_products.json 저장 완료")


if __name__ == "__main__":
    try:
        fetch_ozwear_products()
    except Exception as error:
        print(f"OZWear API 테스트 실패: {error}")
        sys.exit(1)
