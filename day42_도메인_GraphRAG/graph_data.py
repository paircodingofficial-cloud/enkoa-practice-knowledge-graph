"""그래프 적재, 배포 벡터 검사와 결과 출력을 지원한다. 문서 임베딩은 호출하지 않는다."""
import csv
import json
import hashlib
from pathlib import Path

def read_relations(data, path):
    """의료 CSV의 관계를 읽고 기존 논문 관계와 합칩니다."""
    metaedges = {"TREATS": "CtD", "PALLIATES": "CpD", "PRESENTS": "DpS"}
    rows = []
    with path.open(encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            # 기존 예제·골드가 참조하는 Carbidopa→Parkinson's disease의 PALLIATES ID를 유지합니다.
            baseline = (row["source"], row["rel"], row["target"]) == (
                "Compound::DB00190", "PALLIATES", "Disease::DOID:14330"
            )
            rows.append({
                "claim_id": "H01" if baseline else f"H{int(row['original_row']):07d}",
                "subject_id": data["id_prefix"] + row["source"],
                "object_id": data["id_prefix"] + row["target"],
                "relation": row["rel"], "metaedge": metaedges[row["rel"]],
                "evidence": "", "source_doc_id": "hetionet:v1.0",
                "source_kind": "curated", "source_url": "https://het.io/", "properties": {},
            })
    return rows + data["relations"]


def load_graph(path):
    """배포 패킷과 의료 CSV에서 그래프·문서·청크를 읽습니다."""
    from langchain_core.documents import Document

    path = Path(path)
    raw = path.read_bytes()
    data = json.loads(raw)
    signature = hashlib.sha256(raw)
    if "relation_file" in data:
        signature.update((path.parent / data["relation_file"]).read_bytes())
        data["relations"] = read_relations(data, path.parent / data["relation_file"])
    data["fingerprint"] = signature.hexdigest()
    data["chunks"], data["source_links"], data["document_links"] = [], [], []
    for run in data["builder_runs"]:
        document = run["document"]
        for node in run["graph"]["nodes"]:
            if node["label"] == "Chunk":
                assert node["embedding_ref"]["index"] == len(data["chunks"]), "패킷의 벡터 행 순서를 확인하세요."
                data["chunks"].append(Document(
                    page_content=node["properties"]["text"],
                    metadata={"chunk_id": node["id"], "source_doc_id": document["doc_id"],
                              "title": document["title"], "url": document["url"]},
                ))
        for edge in run["graph"]["relationships"]:
            if edge["type"] == "FROM_CHUNK":
                data["source_links"].append({"standard_id": edge["start_node_id"],
                    "chunk_id": edge["end_node_id"], **edge["properties"]})
            elif edge["type"] == "ABOUT_MOVIE":
                data["document_links"].append({"doc_id": edge["start_node_id"],
                                               "standard_id": edge["end_node_id"]})
    return data


def batches(rows, size=5000):
    """모든 행을 빠짐없이 정해진 크기의 묶음으로 반환한다."""
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def prepare_graph(data, run_cypher):
    """조회용 ID 제약과 인덱스를 준비합니다."""
    # 앞 단원 샘플의 이름 유일성 제약은 같은 이름의 조회용 복사본을 허용하지 않습니다.
    # 샘플 설치 스크립트가 만든 두 제약만 ID 제약으로 바꾸며 노드·관계는 보존합니다.
    if data.get("domain") == "movies":
        legacy_constraints = {"movie_title": (["Movie"], ["title"]), "person_name": (["Person"], ["name"])}
        for constraint in run_cypher("SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties RETURN name, labelsOrTypes, properties"):
            name = constraint["name"]
            signature = (constraint["labelsOrTypes"], constraint["properties"])
            if name in legacy_constraints and signature == legacy_constraints[name]:
                run_cypher(f"DROP CONSTRAINT `{name}` IF EXISTS")
                print("조회용 ID 제약으로 전환:", name)
    # 도메인 개체에 RAGEntity를 추가하여 타입 전체에서 standard_id 중복을 막습니다.
    run_cypher("CREATE CONSTRAINT rag_entity_id IF NOT EXISTS FOR (n:RAGEntity) REQUIRE n.standard_id IS UNIQUE")
    run_cypher("CREATE INDEX rag_dataset IF NOT EXISTS FOR (n:RAGEntity) ON (n.dataset)")
    # 기존 과제 그래프도 같은 ID 인덱스를 쓰도록 공통 라벨을 붙입니다.
    run_cypher("MATCH (n) WHERE n.dataset = $dataset AND n.standard_id IS NOT NULL SET n:RAGEntity", dataset=data["dataset"])

def store_graph(data, run_cypher):
    """노드와 관계를 적재하고 처리한 관계 수를 반환합니다."""
    prepare_graph(data, run_cypher)
    dataset = data["dataset"]
    # 재실행해도 같은 standard_id와 claim_id를 MERGE하여 중복을 만들지 않습니다.
    for batch in batches(data["nodes"]):
        run_cypher("""
        UNWIND $nodes AS item
        MERGE (n:RAGEntity {standard_id: item.standard_id})
        SET n:$(item.entity_type)
        SET n += coalesce(item.properties, {})
        SET n.name = item.name, n.entity_type = item.entity_type,
            n.dataset = $dataset, n.aliases = coalesce(item.aliases, [])
        """, nodes=batch, dataset=dataset)

    processed = 0
    # 배치는 저장 요청의 크기이며, 모든 관계를 빠짐없이 처리합니다.
    for batch in batches(data["relations"]):
        result = run_cypher("""
        UNWIND $rows AS item
        MATCH (s:RAGEntity {standard_id: item.subject_id})
        MATCH (o:RAGEntity {standard_id: item.object_id})
        MERGE (s)-[r:$(item.relation) {claim_id: item.claim_id}]->(o)
        SET r += coalesce(item.properties, {})
        SET r.dataset = $dataset, r.evidence = item.evidence,
            r.source_doc_id = item.source_doc_id, r.source_kind = item.source_kind,
            r.source_url = item.source_url, r.metaedge = item.metaedge
        RETURN count(r) AS written
        """, rows=batch, dataset=dataset)
        processed += result[0]["written"]
    assert processed == len(data["relations"]), "연결할 개체가 누락된 관계가 있는지 확인하세요."
    return processed

def read_vectors(path, chunks, embedding_model):
    """배포 벡터가 현재 원문·청크·임베딩 모델과 대응하는지 검사합니다."""
    import numpy as np

    # 파일은 강사가 한 번 생성합니다. 누락 시 학생에게 재임베딩을 시키지 않습니다.
    if not path.exists():
        raise FileNotFoundError(f"배포 임베딩 파일이 없습니다: {path}. data/embeddings 폴더를 확인하세요.")
    ids = [chunk.metadata["chunk_id"] for chunk in chunks]
    signature = hashlib.sha256()
    for chunk in chunks:
        signature.update(json.dumps([chunk.metadata, chunk.page_content], ensure_ascii=False, sort_keys=True).encode())
    fingerprint = signature.hexdigest()
    with np.load(path, allow_pickle=False) as saved:
        # 원문·순서·모델·차원이 다른 벡터를 잘못 적재하지 않도록 확인합니다.
        if saved["fingerprint"].item() != fingerprint or saved["chunk_ids"].tolist() != ids:
            raise ValueError("원문과 배포 임베딩이 다릅니다. 같은 버전의 data 폴더를 사용하세요.")
        if saved["model"].item() != embedding_model.model or int(saved["dimensions"].item()) != embedding_model.dimensions:
            raise ValueError("배포 파일과 질문 임베딩의 모델·차원이 다릅니다.")
        vectors = saved["vectors"]
        if vectors.shape != (len(chunks), embedding_model.dimensions) or not np.isfinite(vectors).all():
            raise ValueError("배포 임베딩의 개수·차원·수치를 확인하세요.")

    return ids, vectors


def source_counts(data, run_cypher):
    """문서·청크·개체 연결을 도메인 관계와 따로 셉니다."""
    counts = run_cypher("""
    MATCH (d:Document) WHERE d.id IN $doc_ids
    OPTIONAL MATCH (c:Chunk)-[source:FROM_DOCUMENT]->(d) WHERE c.id IN $chunk_ids
    OPTIONAL MATCH ()-[r:FROM_CHUNK]->(c) WHERE r.source_dataset = $dataset
    RETURN count(DISTINCT d) AS documents, count(DISTINCT c) AS chunks,
           count(DISTINCT source) AS from_document, count(DISTINCT r) AS from_chunk
    """, doc_ids=list(data["documents"]), dataset=data["dataset"],
         chunk_ids=[chunk.metadata["chunk_id"] for chunk in data["chunks"]])[0]
    if data.get("domain") == "movies":
        counts["about_movie"] = run_cypher("""
        MATCH (d:Document)-[r:ABOUT_MOVIE]->(m:Movie {dataset: $dataset})
        WHERE d.id IN $doc_ids AND r.source_dataset = $dataset
        RETURN count(r) AS count
        """, doc_ids=list(data["documents"]), dataset=data["dataset"])[0]["count"]
    return counts


def store_sources(data, run_cypher, data_dir, embedding_model):
    """저장 패킷의 Document·Chunk·개체 연결과 배포 벡터를 Neo4j에 적재합니다."""
    path = data_dir / data["embedding_file"]
    ids, vectors = read_vectors(path, data["chunks"], embedding_model)
    fingerprint = hashlib.sha256(("neo4j-vector-v1:" + data["fingerprint"] + hashlib.sha256(path.read_bytes()).hexdigest()).encode()).hexdigest()
    # 각 인덱스는 해당 자료의 청크만 검색합니다. 원문 ID와 벡터는 그대로 유지합니다.
    chunk_label = {"movies_complete": "Day42MovieChunk", "paper_focus": "Day42PaperChunk", "drugs": "Day42DrugChunk"}[data["dataset"]]
    expected = {key: data["lexical_counts"][key] for key in ("documents", "chunks", "from_document", "from_chunk")}
    if data.get("domain") == "movies":
        expected["about_movie"] = data["lexical_counts"]["about_movie"]
    saved = run_cypher("MATCH (m:RAGImport {dataset: $dataset}) RETURN m.sources_fingerprint AS fingerprint", dataset=data["dataset"])
    if saved and saved[0]["fingerprint"] == fingerprint and source_counts(data, run_cypher) == expected:
        print("문서·청크·출처 연결 재사용:", expected)
        return

    run_cypher("CREATE CONSTRAINT IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE")
    run_cypher("CREATE CONSTRAINT IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE")
    run_cypher("""
    UNWIND $rows AS item
    MERGE (d:Document {id: item.doc_id})
    SET d.title = item.title, d.url = item.url, d.text = item.text,
        d.license = item.license, d.revision_id = item.revision_id
    """, rows=list(data["documents"].values()))

    # 이전 패킷에서 제외된 청크는 검색 대상 라벨만 제거합니다. 원문 노드는 보존합니다.
    run_cypher("MATCH (c:$($label)) WHERE NOT c.id IN $ids REMOVE c:$($label)", label=chunk_label, ids=ids)

    for indices in batches(list(range(len(ids))), size=256):
        rows = [{"chunk_id": ids[i], "doc_id": data["chunks"][i].metadata["source_doc_id"],
                 "text": data["chunks"][i].page_content, "index": int(ids[i].rsplit(":", 2)[1]),
                 "embedding": vectors[i].tolist()} for i in indices]
        run_cypher("""
        UNWIND $rows AS item
        MATCH (d:Document {id: item.doc_id})
        MERGE (c:Chunk {id: item.chunk_id})
        SET c:$($chunk_label)
        SET c.text = item.text, c.index = item.index,
            c.source_doc_id = d.id, c.title = d.title, c.url = d.url,
            c.embedding = item.embedding, c.embedding_model = $model
        MERGE (c)-[:FROM_DOCUMENT]->(d)
        """, rows=rows, model=embedding_model.model, chunk_label=chunk_label)

    for rows in batches(data["source_links"]):
        run_cypher("""
        UNWIND $rows AS item
        MATCH (e:RAGEntity {standard_id: item.standard_id})
        MATCH (c:Chunk {id: item.chunk_id})
        MERGE (e)-[r:FROM_CHUNK {source_dataset: $dataset}]->(c)
        SET r.link_kind = item.link_kind, r.claim_ids = item.claim_ids
        """, rows=rows, dataset=data["dataset"])
    if data.get("domain") == "movies":
        run_cypher("""
        UNWIND $rows AS item
        MATCH (d:Document {id: item.doc_id})
        MATCH (m:Movie {standard_id: item.standard_id, dataset: $dataset})
        MERGE (d)-[:ABOUT_MOVIE {source_dataset: $dataset}]->(m)
        """, rows=data["document_links"], dataset=data["dataset"])
        # 이전 버전의 영화 전용 연결만 정리합니다. 원본 관계와 의료 근거는 보존합니다.
        run_cypher("""
        MATCH (e:RAGEntity {dataset: $dataset})-[r:FROM_CHUNK]->(c:Chunk)
        WHERE r.source_dataset = $dataset AND c.id IN $chunk_ids
        DELETE r
        """, dataset=data["dataset"], chunk_ids=ids)
    assert source_counts(data, run_cypher) == expected
    run_cypher("MERGE (m:RAGImport {dataset: $dataset}) SET m.sources_fingerprint = $fingerprint",
               dataset=data["dataset"], fingerprint=fingerprint)
    print("문서·청크·출처 연결 적재:", expected)


def collect_response(result, question):
    """이미 실행한 에이전트 결과에서 도구 호출·근거·답변을 모읍니다."""
    from langchain_core.messages import AIMessage, ToolMessage
    calls, rows, chunks, queries = [], [], [], []
    calls_by_id = {}
    for message in result["messages"]:
        # AIMessage의 tool_calls에는 모델이 실제 선택한 도구 이름과 입력이 있습니다.
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                record = {"name": call["name"], "args": call["args"]}
                calls.append(record)
                calls_by_id[call["id"]] = record
        if not isinstance(message, ToolMessage):
            continue
        output = message.content
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                pass  # 오류 안내처럼 JSON이 아닌 응답은 문자열 그대로 남깁니다.
        # 호출 ID로 연결하므로 같은 도구를 여러 번 불러도 응답이 섞이지 않습니다.
        if message.tool_call_id in calls_by_id:
            calls_by_id[message.tool_call_id].update({"output": output, "status": message.status})
        # 이름 후보와 에러 메시지는 근거가 아닙니다. 성공한 검색 결과만 읽습니다.
        if message.status == "success":
            if message.name == "search_graph":
                queries.append(output["cypher"])
                rows.extend(output["rows"])
            elif message.name == "search_documents":
                chunks.extend(output["chunks"])
    answer = result["structured_response"]
    return {"question": question, "tool_calls": calls, "cypher": "\n\n".join(queries),
            "rows": rows, "chunks": chunks,
            "answer": answer.answer, "evidence_ids": answer.evidence_ids}

def show_response(response):
    """도구별 입력·응답, 실행한 Cypher, 최종 답변과 인용을 출력합니다."""
    from pprint import pprint

    print("질문:", response["question"])
    for index, call in enumerate(response["tool_calls"], 1):
        print(f"\n[{index}] 호출 도구: {call['name']}")
        print("입력:")
        pprint(call["args"], sort_dicts=False)
        if "output" in call:
            print("도구 응답:", call["status"])
            if isinstance(call["output"], str):
                print(call["output"])
            else:
                pprint(call["output"], sort_dicts=False)
        else:
            print("도구 응답: 기록 없음")
        print()
    if response["cypher"]:
        print("실행한 Cypher:", response["cypher"])
    print("답변:", response["answer"])
    print("근거 ID:", response["evidence_ids"])
    print()


def show_citations(response):
    """최종 답변과 인용한 근거·출처를 나란히 출력합니다."""
    # 검색 결과에서 ID로 근거를 찾습니다.
    evidence = {}
    for row_number, row in enumerate(response["rows"], 1):
        ids = row["evidence_ids"]
        for cid in ids:
            evidence.setdefault(cid, {})
        for field, key in [("evidence_texts", "text"), ("source_doc_ids", "source"), ("source_kinds", "kind")]:
            values = row.get(field, [])
            if not isinstance(values, list) or len(values) != len(ids):
                print(f"조회 {row_number}행: {field}를 evidence_ids와 같은 순서·개수({len(ids)}개)로 반환하세요.")
                continue  # 대응이 불분명한 열은 임의로 반복하거나 다른 ID에 연결하지 않습니다.
            for cid, value in zip(ids, values):
                evidence[cid][key] = value
    for hit in response["chunks"]:
        evidence[hit["chunk_id"]] = {"text": hit["text"], "source": hit["url"], "kind": hit["title"]}
    print("답변:", response["answer"])
    for cid in response["evidence_ids"]:
        if cid not in evidence:
            print("검색 결과에 없는 인용 ID:", cid)
            continue
        source = evidence[cid]
        print("인용:", cid, "/ 출처:", source.get("kind", "확인 불가"), source.get("source", "확인 불가"))
        print("근거 원문:", source.get("text", "조회 결과에서 원문 대응을 확인할 수 없습니다."))
        print()

def draw_evidence(data, evidence_ids):
    """답변에 인용한 관계를 찾아 작은 방향 그래프로 그립니다."""
    import matplotlib.pyplot as plt
    import networkx as nx

    graph = nx.MultiDiGraph()  # 같은 두 노드의 출처별 관계를 각각 보존합니다.
    names = {node["standard_id"]: node["name"] for node in data["nodes"]}
    node_types = {node["standard_id"]: node["entity_type"] for node in data["nodes"]}
    cited_ids = set(evidence_ids)
    for row in data["relations"]:
        if row["claim_id"] in cited_ids:
            graph.add_edge(row["subject_id"], row["object_id"], key=row["claim_id"], relation=row["relation"])
    # 그림에는 번호·레이블을, 출력에는 전체 이름·레이블을 함께 보여 줍니다.
    labels = {node_id: f"N{index}" for index, node_id in enumerate(graph.nodes, 1)}
    fig, ax = plt.subplots(figsize=(12, 7))
    # 연결이 많은 노드에서 한 홉씩 열을 나누고, 관계 방향은 화살표로 표시합니다.
    pos = nx.bfs_layout(graph.to_undirected(), start=max(graph, key=graph.degree)) if graph else {}
    # 같은 두 노드의 관계도 선을 달리해 그립니다. 근거 ID를 선 위에 표시합니다.
    curves = [f"arc3,rad={0.12 + index * 0.18}" for index in range(graph.number_of_edges())]
    nx.draw_networkx(graph, pos, labels={node_id: f"{label}\n{node_types[node_id]}" for node_id, label in labels.items()},
                     node_color="#bce8e2", node_size=1800, font_size=10,
                     arrows=True, connectionstyle=curves, ax=ax)
    edge_labels = {edge: edge[2] for edge in graph.edges(keys=True)}
    nx.draw_networkx_edge_labels(graph, pos, edge_labels=edge_labels, node_size=1800, font_size=10, label_pos=0.65,
                                 connectionstyle=curves, rotate=False, ax=ax)
    for (start, end, key) in graph.edges(keys=True):
        print(
            labels[start], f"[{node_types[start]}]", "->", graph[start][end][key]["relation"],
            "->", labels[end], f"[{node_types[end]}]", "/ 근거 ID:", key,
        )
    for node_id, label in labels.items():
        print(label, ":", names[node_id], f"[{node_types[node_id]}]")
    ax.margins(0.12)
    ax.set_axis_off()
    plt.show()
    return fig
