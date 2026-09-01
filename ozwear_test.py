import json
import os
import sys

import requests
from requests.auth import HTTPBasicAuth


OZWEAR_API_URL = os.environ.get(
    "OZWEAR_API_URL",
    "https://api.ozwearugg.net/rest/s1/openapi/products",
)

OZWEAR_API_KEY = os.environ.get("OZWEAR_API_KEY")
OZWEAR_API_SECRET = os.environ.get("OZWEAR_API_SECRET")


def save_products(data):
    """API 응답을 JSON 파일로 저장합니다."""

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

        for key in ("products", "data", "items", "result"):
            value = data.get(key)

            if isinstance(value, list):
                print(f"상품 목록으로 추정되는 '{key}' 항목: {len(value)}건")
                break

    print("ozwear_products.json 저장 완료")


def get_response_preview(response):
    """API 오류 내용을 제한된 길이로 보여줍니다."""

    text = response.text.strip()

    if not text:
        return "응답 내용 없음"

    return text[:500]


def fetch_ozwear_products():
    """여러 인증 방식을 순서대로 테스트합니다."""

    if not OZWEAR_API_KEY:
        raise RuntimeError("OZWEAR_API_KEY가 설정되지 않았습니다.")

    if not OZWEAR_API_SECRET:
        raise RuntimeError("OZWEAR_API_SECRET이 설정되지 않았습니다.")

    attempts = [
        {
            "name": "API-Key / API-Secret headers",
            "headers": {
                "Accept": "application/json",
                "API-Key": OZWEAR_API_KEY,
                "API-Secret": OZWEAR_API_SECRET,
            },
            "auth": None,
        },
        {
            "name": "X-API-Key / X-API-Secret headers",
            "headers": {
                "Accept": "application/json",
                "X-API-Key": OZWEAR_API_KEY,
                "X-API-Secret": OZWEAR_API_SECRET,
            },
            "auth": None,
        },
        {
            "name": "HTTP Basic Authentication",
            "headers": {
                "Accept": "application/json",
            },
            "auth": HTTPBasicAuth(
                OZWEAR_API_KEY,
                OZWEAR_API_SECRET,
            ),
        },
    ]

    last_status = None
    last_preview = None

    for attempt_number, attempt in enumerate(attempts, start=1):
        print("")
        print(
            f"인증 방식 {attempt_number}/{len(attempts)} 테스트: "
            f"{attempt['name']}"
        )

        try:
            response = requests.get(
                OZWEAR_API_URL,
                headers=attempt["headers"],
                auth=attempt["auth"],
                timeout=60,
            )

        except requests.exceptions.Timeout:
            print("요청 시간이 초과되었습니다.")
            continue

        except requests.exceptions.RequestException as error:
            print(f"네트워크 요청 실패: {error}")
            continue

        last_status = response.status_code
        last_preview = get_response_preview(response)

        print(f"HTTP status: {response.status_code}")
        print(
            "Content-Type: "
            f"{response.headers.get('Content-Type', '확인 불가')}"
        )

        if response.status_code < 200 or response.status_code >= 300:
            print(f"서버 응답: {last_preview}")
            continue

        try:
            data = response.json()

        except ValueError as error:
            raise RuntimeError(
                "연결은 성공했지만 API 응답이 JSON 형식이 아닙니다."
            ) from error

        print(f"인증 성공: {attempt['name']}")
        save_products(data)
        return

    raise RuntimeError(
        "모든 인증 방식이 실패했습니다. "
        f"마지막 HTTP 상태: {last_status}, "
        f"마지막 서버 응답: {last_preview}"
    )


if __name__ == "__main__":
    try:
        fetch_ozwear_products()

    except Exception as error:
        print("")
        print(f"OZWear API 테스트 실패: {error}")
        sys.exit(1)
