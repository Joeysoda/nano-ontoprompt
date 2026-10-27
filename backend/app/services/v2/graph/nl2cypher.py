"""自然语言 → Cypher 转换服务"""
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CypherPlan:
    cypher: str
    explanation: str
    confidence: float


class NL2CypherService:
    """将自然语言查询转换为 Cypher 语句"""

    def __init__(self, db=None):
        self._db = db

    # 内置安全 Cypher 模式（自然语言关键词 → 模板）
    SAFE_PATTERNS = [
        ("有哪些", "MATCH (n) WHERE n.ontology_id = $ontology_id RETURN n LIMIT 50"),
        ("所有节点", "MATCH (n) WHERE n.ontology_id = $ontology_id RETURN n LIMIT 100"),
        ("关系", "MATCH (n)-[r]->(m) WHERE n.ontology_id = $ontology_id RETURN n, r, m LIMIT 50"),
    ]

    def translate(self, question: str, ontology_schema: dict | None = None) -> CypherPlan:
        """
        将自然语言问题转为 Cypher。
        仅生成候选查询供检查，不可直接执行。模型失败时显式报错。
        """
        try:
            return self._llm_translate(question, ontology_schema or {})
        except Exception as e:
            logger.warning("NL query translation failed: %s", type(e).__name__)
            raise RuntimeError("Natural-language query translation is unavailable") from e

    def _llm_translate(self, question: str, schema: dict) -> CypherPlan:
        schema_str = json.dumps(schema, ensure_ascii=False, indent=2) if schema else "（未知模式）"
        prompt = f"""请将以下自然语言问题转换为 Neo4j Cypher 查询语句。

图模式：
{schema_str}

问题：{question}

要求：
1. 只生成 MATCH/RETURN/WHERE/WITH 语句（不允许 CREATE/DELETE/SET/MERGE）
2. 使用 $ontology_id 参数过滤本体范围
3. LIMIT 不超过 200

返回 JSON（仅此结构，无其他文字）：
{{"cypher": "MATCH ...", "explanation": "查询说明", "confidence": 0.9}}"""

        from app.services import llm_service
        from app.services.model_config_selector import llm_call_kwargs, select_llm_model_config
        call_kwargs = llm_call_kwargs(select_llm_model_config(
            self._db,
            purpose_tags=("NL2Cypher", "图谱查询", "Cypher生成"),
            allow_vlm=False,
        ))
        if not call_kwargs:
            raise RuntimeError("No LLM model config available for NL2Cypher")
        raw = llm_service._call_llm(
            **call_kwargs,
            messages=[
                {"role": "system", "content": "你是 Neo4j Cypher 专家。只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
        )
        data = json.loads(raw) if isinstance(raw, str) else raw
        cypher = data.get("cypher", "")
        # 安全检查
        self._validate_read_only(cypher)
        return CypherPlan(
            cypher=cypher,
            explanation=data.get("explanation", ""),
            confidence=float(data.get("confidence", 0.7)),
        )

    def _rule_translate(self, question: str) -> CypherPlan:
        """规则模板匹配"""
        q_lower = question.lower()
        for keyword, cypher in self.SAFE_PATTERNS:
            if keyword in q_lower or keyword in question:
                return CypherPlan(
                    cypher=cypher,
                    explanation=f"规则匹配：包含关键词「{keyword}」",
                    confidence=0.5,
                )
        raise ValueError("Unsupported question; specify an Object Query instead")

    @staticmethod
    def _validate_read_only(cypher: str):
        """Reject obvious unsafe drafts; this is NOT an authorization boundary.

        The execution endpoint is retired. Even a passing draft must never be
        executed on behalf of a user without a policy-aware compiler.
        """
        upper = cypher.upper().strip()
        for kw in ("CREATE", "MERGE", "DELETE", "DETACH", "SET ", "REMOVE", "DROP"):
            if kw in upper:
                raise ValueError(f"Cypher 包含写操作关键词 {kw}，已阻断")
        if any(token in cypher for token in (";", "//", "/*", "*/")) or re.search(r"\b(CALL|LOAD|UNION|FOREACH)\b", upper):
            raise ValueError("Unsupported query construct")
        if not re.search(r"\b\w+\.ontology_id\s*=\s*\$ontology_id\b", cypher, re.IGNORECASE):
            raise ValueError("Query must declare an ontology scope")
        limit = re.search(r"\bLIMIT\s+(\d+)\s*$", cypher, re.IGNORECASE)
        if not limit or not 1 <= int(limit.group(1)) <= 200:
            raise ValueError("Query must end with a literal LIMIT between 1 and 200")
