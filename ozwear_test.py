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


def try_request(method, headers, auth, params=None, json_body=None):
    """지정된 방식으로 요청 1회를 시도하고 응답을 반환합니다."""

    try:
        response = requests.request(
            method,
            OZWEAR_API_URL,
            headers=headers,
            auth=auth,
            params=params,
            json=json_body,
            timeout=60,
        )
        return response, None

    except requests.exceptions.Timeout:
        return None, "요청 시간이 초과되었습니다."

    except requests.exceptions.RequestException as error:
        return None, f"네트워크 요청 실패: {error}"


def fetch_ozwear_products():
    """여러 인증 방식 x HTTP 메서드 조합을 순서대로 테스트합니다."""

    if not OZWEAR_API_KEY:
        raise RuntimeError("OZWEAR_API_KEY가 설정되지 않았습니다.")

    if not OZWEAR_API_SECRET:
        raise RuntimeError("OZWEAR_API_SECRET이 설정되지 않았습니다.")

    # 로그 분석 결과: Basic Auth는 403이 아니라 405(GET 미지원)였음.
    # → 인증 자체는 Basic Auth가 맞을 가능성이 높고, 메서드가 문제였을 가능성이 큼.
    # 그래서 Basic Auth를 먼저, 그리고 GET과 POST를 모두 시도한다.
    attempts = [
        {
            "name": "HTTP Basic Auth + GET",
            "method": "GET",
            "headers": {"Accept": "application/json"},
            "auth": HTTPBasicAuth(OZWEAR_API_KEY, OZWEAR_API_SECRET),
            "params": None,
            "json_body": None,
        },
        {
            "name": "HTTP Basic Auth + POST (빈 body)",
            "method": "POST",
            "headers": {"Accept": "application/json", "Content-Type": "application/json"},
            "auth": HTTPBasicAuth(OZWEAR_API_KEY, OZWEAR_API_SECRET),
            "params": None,
            "json_body": {},
        },
        {
            "name": "HTTP Basic Auth + GET (Content-Type 없이, 쿼리파라미터 포함)",
            "method": "GET",
            "headers": {"Accept": "application/json"},
            "auth": HTTPBasicAuth(OZWEAR_API_KEY, OZWEAR_API_SECRET),
            "params": {"limit": 50, "offset": 0},
            "json_body": None,
        },
        {
            "name": "API-Key / API-Secret headers + GET",
            "method": "GET",
            "headers": {
                "Accept": "application/json",
                "API-Key": OZWEAR_API_KEY,
                "API-Secret": OZWEAR_API_SECRET,
            },
            "auth": None,
            "params": None,
            "json_body": None,
        },
        {
            "name": "X-API-Key / X-API-Secret headers + GET",
            "method": "GET",
            "headers": {
                "Accept": "application/json",
                "X-API-Key": OZWEAR_API_KEY,
                "X-API-Secret": OZWEAR_API_SECRET,
            },
            "auth": None,
            "params": None,
            "json_body": None,
        },
    ]

    last_status = None
    last_preview = None

    for attempt_number, attempt in enumerate(attempts, start=1):
        print("")
        print(f"시도 {attempt_number}/{len(attempts)}: {attempt['name']}")

        response, error = try_request(
            attempt["method"],
            attempt["headers"],
            attempt["auth"],
            attempt.get("params"),
            attempt.get("json_body"),
        )

        if error:
            print(error)
            continue

        last_status = response.status_code
        last_preview = get_response_preview(response)

        print(f"HTTP status: {response.status_code}")
        print(f"Content-Type: {response.headers.get('Content-Type', '확인 불가')}")

        if response.status_code < 200 or response.status_code >= 300:
            print(f"서버 응답: {last_preview}")
            continue

        try:
            data = response.json()

        except ValueError as error:
            raise RuntimeError(
                "연결은 성공했지만 API 응답이 JSON 형식이 아닙니다. "
                f"응답 미리보기: {last_preview}"
            ) from error

        print(f"성공: {attempt['name']}")
        save_products(data)
        return

    raise RuntimeError(
        "모든 조합이 실패했습니다. "
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
