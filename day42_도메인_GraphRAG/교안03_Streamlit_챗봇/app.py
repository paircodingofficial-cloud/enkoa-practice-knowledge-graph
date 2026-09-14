"""실행: streamlit run app.py"""
import json
from textwrap import fill

import streamlit as st

# 연결·검색기는 공유하고, 각 사용자의 대화는 아래 session_state에 따로 저장합니다.
@st.cache_resource
def load_chatbot():
    """화면을 다시 그려도 연결과 검색 저장소를 재사용합니다."""
    # 준비 에러도 main에서 안내할 수 있게 여기서 검색 모듈을 불러옵니다.
    import chatbot

    return chatbot


def evidence_label(evidence_id):
    """청크 ID는 문서와 번호를 표시하고 긴 버전 해시는 상세 근거에 남깁니다."""
    return evidence_id.rsplit(":", 1)[0] if ":chunk:" in evidence_id else evidence_id


def add_node(statements, node_id, label):
    """이름의 따옴표와 줄바꿈을 보존해 노드 선언을 추가합니다."""
    # ID는 같은 개체를 연결하는 키이고 label은 사람이 읽는 이름입니다.
    statements.add(f'{json.dumps(node_id)} [label={json.dumps(label, ensure_ascii=False)}];')


def add_edge(statements, start, end, label, style="solid"):
    """동일한 연결은 한 번만 넣고 관계 ID가 다르면 각각 보존합니다."""
    statements.add(f'{json.dumps(start)} -> {json.dumps(end)} '
                   f'[label={json.dumps(label, ensure_ascii=False)}, style={style}];')


def get_evidence(data, response):
    """검색 결과와 그 결과에 저장된 원문 연결을 모읍니다."""
    evidence_ids = {cid for row in response["rows"] for cid in row["evidence_ids"]}
    chunk_ids = {hit["chunk_id"] for hit in response["chunks"]}
    # 벡터로 찾은 청크에 실제로 연결된 관계를 포함합니다.
    vector_links = [link for link in data["source_links"] if link["chunk_id"] in chunk_ids]
    evidence_ids.update(cid for link in vector_links for cid in link["claim_ids"])
    relations = [row for row in data["relations"] if row["claim_id"] in evidence_ids]
    # 관계만 조회한 경우에도 그 관계 ID의 근거 청크까지 따라갑니다.
    links = [link for link in data["source_links"]
             if link["chunk_id"] in chunk_ids or evidence_ids.intersection(link["claim_ids"])]
    source_ids = {link["chunk_id"] for link in links}
    chunks = {hit["chunk_id"]: hit for hit in response["chunks"]}
    for chunk in data["chunks"]:
        chunk_id = chunk.metadata["chunk_id"]
        if chunk_id in source_ids and chunk_id not in chunks:
            chunks[chunk_id] = {**chunk.metadata, "text": chunk.page_content}
    return relations, list(chunks.values()), links


def make_graph(data, response):
    """검색한 관계와 그 근거의 개체→청크→문서 연결을 DOT 그래프로 반환합니다."""
    relations, chunks, links = get_evidence(data, response)
    nodes = {node["standard_id"]: node for node in data["nodes"]}
    statements = set()

    for row in relations:
        for node_id in (row["subject_id"], row["object_id"]):
            item = nodes[node_id]
            add_node(statements, node_id, f'{fill(item["name"], width=28)}\n({item["entity_type"]})')
        add_edge(statements, row["subject_id"], row["object_id"], f'{row["relation"]}\n{row["claim_id"]}')

    for hit in chunks:
        doc_id = "doc:" + hit["source_doc_id"]
        add_node(statements, doc_id, f'{fill(hit["title"], width=32)}\n({hit["source_doc_id"]})')
        add_node(statements, hit["chunk_id"], evidence_label(hit["chunk_id"]))
        # 점선은 치료 관계가 아니라 저장된 출처의 방향(개체->청크->문서)을 나타냅니다.
        add_edge(statements, hit["chunk_id"], doc_id, "FROM_DOCUMENT", "dashed")
    for link in links:
        item = nodes[link["standard_id"]]
        add_node(statements, link["standard_id"], f'{fill(item["name"], width=28)}\n({item["entity_type"]})')
        add_edge(statements, link["standard_id"], link["chunk_id"], "FROM_CHUNK", "dashed")

    if not statements:
        return ""
    # 출처까지 가로로 늘어져 글자가 작아지지 않도록 위에서 아래로 배치합니다.
    return "digraph {\nrankdir=TB;\n" + "\n".join(sorted(statements)) + "\n}"


def answer_text(response):
    """답변 문자열 뒤에 사용한 근거 ID를 표시합니다."""
    citations = ", ".join(evidence_label(cid) for cid in response["evidence_ids"])
    if citations:
        return f'{response["answer"]}\n\n근거: {citations}'
    return response["answer"]


def show_response(data, response):
    """실제 검색 방식, 답변, 검색 그래프, 상세 근거를 함께 보여줍니다."""
    used = {call["name"] for call in response["tool_calls"]}
    vector = "선택됨" if "search_documents" in used else "선택 안 됨"
    cypher = "선택됨" if "search_graph" in used else "선택 안 됨"
    st.caption(f"벡터 의미 검색: {vector} | Text2Cypher: {cypher}")
    st.markdown(answer_text(response))

    graph = make_graph(data, response)
    if graph:
        st.write("검색된 그래프")
        st.graphviz_chart(graph, width="stretch")
        st.caption("실선: 의료 관계. 점선: 개체·청크·문서를 잇는 저장된 출처 연결.")
    else:
        st.caption("이번 검색에서 시각화할 관계나 원문 청크가 없습니다.")

    with st.expander("검색 과정과 근거 확인"):
        # 답변 아래에서는 해시를 줄여 표시하고, 여기서는 원래 ID를 확인합니다.
        st.write("답변의 인용 ID:", response["evidence_ids"])
        tool_names = {"select_names": "이름·별칭 확인", "search_graph": "Text2Cypher 관계 조회",
                      "search_documents": "벡터 의미 검색"}
        # 선택 여부와 순서는 실제 호출 기록에서 읽습니다.
        st.write("도구 요청 순서")
        for index, call in enumerate(response["tool_calls"], 1):
            st.write(f'{index}. {tool_names.get(call["name"], call["name"])}')
            if call["name"] == "select_names":
                st.write("확인할 이름:", call["args"].get("names", []))
            elif call["name"] == "search_documents":
                st.write("검색어:", call["args"].get("query", ""))
            if "output" in call:
                st.write("도구 응답:", call["status"])
                st.write(call["output"])
        if response["cypher"]:
            st.write("실행한 Cypher")
            st.code(response["cypher"], language="cypher")
            st.dataframe(response["rows"], hide_index=True)

    _, chunks, _ = get_evidence(data, response)
    if chunks:
        with st.expander("근거 청크와 논문 원문"):
            for hit in chunks:
                st.write(evidence_label(hit["chunk_id"]))
                if "score" in hit:
                    st.caption(f'Neo4j 벡터 유사도: {hit["score"]:.3f} (클수록 유사함)')
                else:
                    st.caption("조회한 관계의 근거로 연결된 청크")
                st.write(hit["text"])
                st.link_button(hit["title"], hit["url"])


def main():
    """한 페이지에서 대화 기록을 유지하며 질문을 처리합니다."""
    st.title("의료 GraphRAG 챗봇")
    st.caption("질문에 따라 관계 조회와 원문 의미 검색을 선택합니다.")
    try:
        with st.spinner("그래프와 검색 저장소를 준비하고 있습니다."):
            chatbot = load_chatbot()
            data = chatbot.paper
    except Exception as exc:
        st.error(f"준비 에러: {type(exc).__name__}. .streamlit/secrets.toml의 연결 정보·Neo4j·배포 파일을 확인하세요.")
        st.stop()

    # 화면용 기록과 모델용 메시지를 분리하되 둘 다 브라우저 세션에 저장합니다.
    if "messages" not in st.session_state:
        st.session_state.messages = []
        st.session_state.history = []
    if st.button("대화 지우기"):
        st.session_state.messages = []
        st.session_state.history = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.write(message["content"])
            else:
                show_response(data, message["response"])

    question = st.chat_input("예: Gabapentin이 완화하는 것으로 보고된 증상은 무엇인가요?")
    if not question or not question.strip():
        return
    question = question.strip()
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        answer_area = st.empty()
        try:
            with st.spinner("근거를 검색하고 답변을 생성하고 있습니다."):
                response, history = chatbot.ask(
                    question, st.session_state.history, on_answer=answer_area.markdown,
                )
            # 스트리밍 영역을 검증한 최종 답변·그래프로 교체해 본문이 중복되지 않게 합니다.
            with answer_area.container():
                show_response(data, response)
        except ValueError as exc:
            answer_area.empty()
            # 근거 검증 실패를 빈 검색 결과나 연결 에러로 안내하지 않습니다.
            st.error(str(exc))
            return
        except Exception as exc:
            answer_area.empty()
            st.error(f"검색 에러: {type(exc).__name__}. 연결 상태를 확인하고 다시 질문하세요.")
            return
    # 성공적으로 처리한 질문만 저장하므로 중단된 질문은 다음 대화에 섞이지 않습니다.
    st.session_state.history = history
    st.session_state.messages.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "response": response},
    ])


if __name__ == "__main__":
    main()
