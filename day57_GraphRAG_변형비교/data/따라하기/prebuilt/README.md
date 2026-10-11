# 미리 만든 따라하기 색인

교안 01·02 함께 따라하기의 색인을 강사가 같은 설정으로 미리 만들어 둔 것입니다. 색인 셀을 기다리기 어려우면 색인 셀 대신 바로 아래의 복사 셀을 실행합니다. 두 셀 가운데 하나만 실행합니다.

| 경로 | 내용 | 복사되는 곳 |
|---|---|---|
| `graphrag/` | Microsoft GraphRAG 색인 결과(parquet 테이블 6개, `lancedb/` 벡터 저장소, `stats.json`, `context.json`) | `output/graphrag_따라하기/output/` |
| `lightrag_incident/` | LightRAG `incident` workspace(그래프 graphml, kv 저장소, 벡터 저장소). LLM 응답 캐시는 문서 삭제 때 개체 설명을 다시 만드는 추출 결과(20편)만 남겼습니다 | `output/lightrag/incident/` |
| `index_usage.json` | 강사 색인의 LLM 호출 수, 입력·출력 토큰, 걸린 초 | - |

## 만든 조건

- 문서: `data/따라하기/docs/` 의 20편(`extra/INC-0922.txt` 는 넣지 않음). GraphRAG 문서 제목과 LightRAG 청크의 `file_path` 는 `<doc_id>.txt` 입니다.
- 모델: 답변·추출 `gpt-5.6-luna`, 임베딩 `text-embedding-3-large`(768차원)
- GraphRAG(`graphrag` 3.1.0): 개체 유형 서비스·구성요소·팀·인물·장애, `max_gleanings` 0, `use_lcc` false, 한국어 지시를 넣은 추출·요약·보고서 프롬프트(교안 01이 만드는 프롬프트와 같음)
- LightRAG(`lightrag-hku` 1.5.7): workspace `incident`, 언어 Korean, 개체 유형 안내 "서비스, 구성요소, 팀, 인물, 장애 중 하나로 분류한다. 맞는 것이 없으면 Other.", `entity_extract_max_gleaning` 0
- 만든 날: GraphRAG 2026-10-08, LightRAG 2026-10-10
- GraphRAG 입력은 문서 첫 줄의 제목이 둘째 줄에 한 번 더 들어간 판입니다. 그 줄을 빼면 지금 `docs/` 와 같습니다. LightRAG 입력은 지금 `docs/` 와 같습니다.
- `graphrag/lancedb/` 는 측정 색인의 벡터를 그대로 읽어 `graphrag` 3.1.0 환경에서 다시 저장한 것입니다.

LLM이 만든 결과라 직접 색인하면 개체·관계·보고서 수가 조금 다를 수 있습니다.

## 출처

원문은 `data/따라하기/` 와 같은 수업용 가상 회사 「누리페이」의 사내 문서입니다. 색인 안의 개체·관계 설명과 보고서는 그 원문에서 LLM이 만든 것입니다. 자세한 내용은 [따라하기 자료 설명](../README.md)에 있습니다.
