"""부록의 공개 Wikidata 검색과 상세 정보 조회 도구."""
import requests

API_URL = "https://www.wikidata.org/w/api.php"
HEADERS = {"User-Agent": "EntityLinkingLesson/1.0 (educational Wikidata lookup)"}


def get_json(params):
    """Wikidata API에 요청하고 JSON 응답을 읽습니다.

    Args:
        params (dict): 검색어, 조회할 ID 등 API 요청 조건.
    Returns:
        dict: API가 반환한 응답.
    """
    response = requests.get(API_URL, params={**params, "format": "json"},
                            headers=HEADERS, timeout=30)
    return response.json()


def search_candidates(terms):
    """이름과 별칭으로 후보를 검색하고 같은 Q-ID의 중복을 제거합니다.

    Args:
        terms (list[str]): 검색할 이름과 별칭. 예: ["LangChain", "랭체인"].
    Returns:
        dict: {Q-ID: 검색 결과} 사전. 각 결과에 id, label, description이 있습니다.
    """
    found = {}
    for term in terms:
        result = get_json({"action": "wbsearchentities", "search": term,
                           "language": "ko", "uselang": "ko", "limit": 5})
        for item in result["search"]:
            found[item["id"]] = item
    return found


def get_entities(ids):
    """Q-ID에 해당하는 항목의 이름, 설명과 속성을 조회합니다.

    Args:
        ids (list[str]): 조회할 Q-ID 목록.
    Returns:
        dict: {Q-ID: 항목 상세 정보}. 입력이 비어 있으면 {}.
    """
    if not ids:
        return {}
    return get_json({"action": "wbgetentities", "ids": "|".join(ids),
                     "props": "labels|descriptions|claims", "languages": "ko|en|mul"})["entities"]


def claim_values(entity, property_id):
    """항목에서 지정한 속성의 값을 꺼냅니다. 폐기된 진술은 제외합니다.

    Args:
        entity (dict): 항목 상세 정보. property_id (str): P31처럼 조회할 속성 ID.
    Returns:
        list: 실제 값이 있는 속성값 목록.
    """
    values = []
    for claim in entity.get("claims", {}).get(property_id, []):
        if claim.get("rank") == "deprecated":
            continue
        value = claim["mainsnak"].get("datavalue")
        if value is not None:
            values.append(value["value"])
    return values


def text_of(entity, field):
    """한국어, 영어, 언어 공통 표기 순으로 이름 또는 설명을 고릅니다.

    Args:
        entity (dict): 항목 정보. field (str): labels 또는 descriptions.
    Returns:
        str: 표시할 문자열. 찾지 못하면 "정보 없음".
    """
    for language in ["ko", "en", "mul"]:
        if language in entity.get(field, {}):
            return entity[field][language]["value"]
    return "정보 없음"


def candidate_rows(ids):
    """후보들의 상세 정보를 조회해 사람이 비교할 표의 행으로 정리합니다.

    Args:
        ids (list[str]): 후보 Q-ID 목록. 예: ["Q117340550"].
    Returns:
        list[dict]: qid, name, type, description, websites가 담긴 후보별 행 목록.
    """
    entities = get_entities(ids)
    type_ids = set()
    for entity in entities.values():
        for value in claim_values(entity, "P31"):
            type_ids.add(value["id"])
    type_entities = get_entities(sorted(type_ids))
    rows = []
    for qid, entity in entities.items():
        type_names = []
        for value in claim_values(entity, "P31"):
            type_names.append(text_of(type_entities[value["id"]], "labels"))
        rows.append({"qid": qid, "name": text_of(entity, "labels"),
                     "type": ", ".join(type_names) or "정보 없음",
                     "description": text_of(entity, "descriptions"),
                     "websites": claim_values(entity, "P856")})
    return rows
