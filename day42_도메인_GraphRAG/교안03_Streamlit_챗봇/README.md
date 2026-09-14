# 교안 03. 검색 그래프를 보여주는 Streamlit 챗봇

교안 01·02에서 만든 의료 검색 도구를 한 페이지 챗봇에 연결합니다. 모든 코드는 완성되어 있습니다.

## 실행합니다

저장소 최상위에서 앱 폴더로 이동한 뒤 실행합니다. Streamlit은 **실행한 폴더**의 `.streamlit/secrets.toml`을 읽습니다.

```bash
cd day42_도메인_GraphRAG/교안03_Streamlit_챗봇
uv run --with-requirements requirements.txt streamlit run app.py --server.fileWatcherType none
```

별도 Python 환경에서는 **`교안03_Streamlit_챗봇` 폴더의 터미널**에서 필요한 패키지를 설치한 뒤 실행합니다.

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.fileWatcherType none
```

`--server.fileWatcherType none`은 공용 실습 환경의 다른 모델 패키지에서 발생하는 파일 감시 에러를 막습니다. 코드를 수정한 뒤에는 앱을 다시 실행합니다.

상위 교안의 `graph_data.py`, `data/paper_extraction_packet.json`, 해당 JSON이 참조하는 의료 CSV, `data/paper_schema.json`, `data/embeddings/paper_focus.npz`가 필요합니다. **교안03 폴더는 상위 교안 폴더 안에 둡니다.** 앞 노트북을 실행하지 않았어도 앱이 같은 자료를 준비합니다.

## 연결 정보를 설정합니다

앱 폴더의 `.streamlit/secrets.toml`에 연결 정보를 넣고 Neo4j를 실행해 둡니다. 파일이 없으면 `.streamlit/secrets.toml.example`을 같은 폴더의 `secrets.toml`로 복사해 값을 입력합니다.

```toml
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "YOUR_NEO4J_PASSWORD"
```

`chatbot.py`에서 `st.secrets[...]`로 읽어 Neo4j 드라이버와 LLM·임베딩 모델에 전달합니다. 실제 값이 담긴 파일은 `.gitignore`에 등록되어 있습니다. 연결 정보를 변경하면 앱을 다시 실행합니다. [Streamlit Secrets 안내](https://docs.streamlit.io/develop/concepts/connections/secrets-management)

문서 벡터는 배포 파일을 재사용하고 검색 질문만 임베딩합니다. 답변 생성에는 앞 교안과 같은 `gpt-5.6-luna`를 사용합니다.

## 두 파일을 순서대로 읽습니다

| 파일 | 핵심 역할 |
|---|---|
| [chatbot.py](chatbot.py) | 연결 → 그래프·벡터 준비 → Text2Cypher·벡터 도구 → 에이전트 → 답변 |
| [app.py](app.py) | 대화 입력 → 실제 검색 방식 표시 → 답변 → 검색된 그래프·근거 표시 |

`run_cypher`, `read_query`, `VectorRetriever`는 앞 교안의 방식을 유지합니다. `chatbot.py`는 함수를 정의한 뒤 마지막 준비 코드에서 연결·저장소·에이전트를 한 번 만듭니다. `select_names`, `search_graph`, `search_documents`를 직접 정의해 에이전트에 전달합니다. 이름 후보는 Neo4j에서 조회합니다.

도구 인수도 교안 02와 같습니다. `select_names(dataset, names)`, `search_graph(cypher)`, `search_documents(dataset, query)`를 사용하며, 이 앱의 검색 대상은 `paper_focus`입니다. `ChatPromptTemplate`에 의료 스키마와 조회 규칙을 넣고, 채팅에 필요한 후속 질문의 대상 확인·재검색 지침을 추가합니다.

1. `create_agent`가 이름 조회와 두 검색 도구의 설명을 읽고 질문에 필요한 도구를 호출합니다.
2. 필요하면 `select_names`로 Neo4j의 이름·별칭을 확인합니다. 에이전트가 스키마에 맞게 Cypher를 작성하고 `search_graph(cypher)`가 조회를 실행합니다.
3. `search_documents`는 호출마다 질문 벡터로 가까운 원문 청크를 최대 3개 찾습니다. 필요하면 에이전트가 질문을 바꾸어 추가 검색합니다.
4. `ask`는 `agent.stream(..., stream_mode=["messages", "values"])`으로 생성 중인 답변과 실행 상태를 받습니다. `partial_answer`가 생성 중인 JSON의 `answer`만 읽고, `on_answer`로 전달한 `st.empty().markdown`이 화면을 바로 갱신합니다. JSON 키·인용 ID 목록·도구 인수는 답변 본문에 흘리지 않습니다.
5. 생성이 끝나면 **이번 질문의 호출 기록만** 모아 답변·인용을 검사합니다. 실패하면 초안을 지우고 같은 검색 근거로 한 번 수정하며, 계속 실패하거나 연결이 끊기면 초안을 지우고 대화에 저장하지 않습니다. 성공하면 같은 화면 영역에 최종 답변·인용·그래프를 표시합니다.
6. `show_response`가 실제 도구 요청으로 두 검색의 선택 여부를 표시합니다. 둘을 호출했다면 둘 다 선택됨으로 나옵니다. 상세 화면에서 이름 확인·관계 조회·원문 검색의 요청 순서와 실행한 Cypher를 확인합니다.
7. `make_graph`는 검색 결과의 ID로 그래프를 만듭니다. 그래프 문법 문자열을 Streamlit 기본 차트에 전달하므로 HTML·CSS 파일이나 별도 웹 서버는 필요하지 않습니다.

스트리밍에서 `messages`는 모델의 토큰과 메타데이터를, `values`는 각 단계의 전체 상태를 전달합니다. [LangGraph 스트림 모드](https://reference.langchain.com/python/langgraph/types/StreamMode)

LLM의 최종 출력은 두 필드입니다. `ProviderStrategy(GroundedAnswer, strict=True)`에 클래스를 직접 전달하며, `structured_response`에서 `GroundedAnswer` 객체를 받습니다.

| 필드 | 내용 |
|---|---|
| `answer` | 검색 근거로 작성한 답변 문자열 |
| `evidence_ids` | 답변에 사용한 관계·청크 ID 목록 |

`collect_response`가 이 두 필드에 `tool_calls`, `cypher`, `rows`, `chunks` 등 실행 기록을 더합니다. 화면에서는 `response["answer"]`와 `response["evidence_ids"]`를 바로 읽습니다. 근거가 없으면 답변에 부족한 점을 설명하고 ID 목록은 비웁니다. ID 검사는 인용의 존재 여부만 확인하므로 답변 내용은 원문과 대조합니다.

## 그래프를 읽습니다

- **Text2Cypher:** 조회 결과의 `evidence_ids`에 해당하는 실제 관계와 양 끝 개체를 그립니다. 그 관계의 저장된 근거 청크·논문 문서도 연결합니다. 예를 들어 `P04`만 조회하면 같은 청크의 `P05`, `P06` 관계가 추가되지는 않습니다.
- **벡터 의미 검색:** 검색된 청크에서 원문으로 `FROM_DOCUMENT`를 그립니다. 그 청크에 저장된 `FROM_CHUNK` 개체 연결과 근거 관계가 있으면 함께 그립니다. 같은 논문의 다른 청크에서 나온 관계는 추가하지 않습니다.
- 실선은 저장된 의료 관계입니다. 점선은 저장된 개체·청크·문서 출처 연결입니다.
- 검색된 청크에 저장된 관계가 없다면 원문·청크만 표시합니다. 의미가 비슷하다는 이유로 새 의료 관계를 만들지 않습니다.
- 그림은 검색한 근거의 범위를 보여줍니다. 각 답변이 실제로 인용한 근거는 답변 뒤의 ID로 확인합니다.
- `검색 과정과 근거 확인`에서 전체 인용 ID·도구 요청·Cypher 결과를, `근거 청크와 논문 원문`에서 본문과 논문 링크를 확인합니다. 답변·그림에서는 긴 청크 버전 해시를 생략합니다.
- 관계의 출처를 따라 찾은 청크에는 벡터 유사도 점수를 붙이지 않습니다. 이때 벡터 검색 선택 여부도 바뀌지 않습니다.

## 질문을 바꿔 확인합니다

| 질문 | 확인할 동작 |
|---|---|
| Gabapentin이 완화 목적으로 사용되었다고 보고된 증상은 무엇인가요? | Text2Cypher, 생성 쿼리, 관계 그래프 |
| Laquinimod 임상시험 중 원문에 연구 단계가 명시된 시험과 그 단계를 알려 주세요. | 벡터 의미 검색, 원문·청크 그래프, 출처 |
| 저장된 Carbidopa의 치료 관계를 조회하고, Laquinimod 임상시험 중 원문에 단계가 명시된 시험과 그 단계를 찾아 각각 설명해 주세요. | 두 도구의 실제 호출 |
| 저장된 ZZZ없는약의 치료 관계를 알려 주세요. | 빈 검색 결과와 근거 부족 안내 |

먼저 Gabapentin을 질문한 뒤 `그 약이 완화하는 질환도 알려 주세요.`를 입력해 후속 질문을 확인합니다. `대화 지우기`를 누르면 화면 기록과 모델 기록이 함께 비워집니다.

준비 에러는 `.streamlit/secrets.toml`, 앱 폴더에서 실행했는지, Neo4j 실행 상태, 배포 파일을 확인합니다. 검색 에러는 API 연결·사용량과 Neo4j 연결을 확인합니다. 빈 조회는 에러와 구분해 근거 부족으로 표시합니다. 인용을 확인하지 못하면 답변을 기록하지 않고 다시 질문하도록 안내합니다.

관련 API: [Streamlit 채팅 입력](https://docs.streamlit.io/develop/api-reference/chat/st.chat_input), [그래프 표시](https://docs.streamlit.io/develop/api-reference/charts/st.graphviz_chart), [연결 캐시](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_resource).

검색은 교안 01·02와 같은 Neo4j `Chunk.embedding` 인덱스와 `VectorRetriever`를 사용합니다. Neo4j 2026.01 이상을 사용하며, 문서 벡터는 다시 생성하지 않습니다.
