"""
记忆治理安全与准入拦截策略 (MemoryPolicyGuard)
核心职责：
1. 写入准入白名单门控：严禁大模型普通推演或 Plan-First 未批方案污染结构化事实库，仅放行高置信度信号
2. 统一隐私脱敏网关：在落盘 event_ledger / memory_facts / session_summaries 及组装 Prompt 前掩码过滤敏感凭据与个人隐私
"""
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any


class MemoryAdmissionError(ValueError):
    """当写入请求违反记忆准入策略时抛出的拦截异常"""


@dataclass
class MemoryWriteDecision:
    """记忆写入准入评估结果"""
    allowed: bool
    reason: str
    sanitized_text: str = ""
    redactions: list[str] = field(default_factory=list)


class MemoryPolicyGuard:
    """
    记忆写入准入控制与隐私脱敏守卫
    """

    # NOTE: 仅允许 5 类确定性高置信来源进入 memory_facts，杜绝模型自由幻想污染确定事实视图
    ALLOWED_SOURCE_TYPES: frozenset[str] = frozenset({
        "explicit_cmd",   # 用户在对话中显式声明偏好/规则，或显式调用记忆工具保存
        "inquiry",        # 用户在 interactive_inquiry 对齐卡中确认的选择、审批或填空
        "verified_env",   # TaskLoopRunner / AgentRuntime 工具实际执行验证成功的环境事实
        "manual",         # 用户在 Memory Hub 界面手动新增/修改
        "rollback",       # 用户或系统触发的历史版本时间轴回滚恢复
    })

    # 仅在以下来源下允许在 plan_first 只读期写入（代表人类主动确认或显式授权）
    PLAN_FIRST_ALLOWED_SOURCES: frozenset[str] = frozenset({
        "explicit_cmd",
        "inquiry",
        "manual",
        "rollback",
    })

    # PEM 私钥块（跨行）
    _PEM_PRIVATE_KEY_RE = re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
    )
    # 数据库连接串中的密码段（保留协议、用户名与主机，仅脱敏密码）
    _DB_URI_PASSWORD_RE = re.compile(
        r"((?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|amqp|mssql|oracle)://[^:\s/@]+:)([^@\s/]+)(@)",
        re.IGNORECASE,
    )
    # 常见云商与模型 API Key
    _OPENAI_STYLE_KEY_RE = re.compile(r"\b(?:sk-proj-|sk-ant-|sk-)[A-Za-z0-9_-]{20,}\b")
    _GOOGLE_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b")
    _GITHUB_TOKEN_RE = re.compile(r"\b(?:(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
    _AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
    # Bearer 认证令牌
    _BEARER_TOKEN_RE = re.compile(
        r"(Bearer\s+)(?!\[REDACTED:)[A-Za-z0-9\-._~+/]{16,}=*",
        re.IGNORECASE,
    )
    # 常见敏感键值对赋值（api_key=, secret=, password= 等）
    _SENSITIVE_KV_RE = re.compile(
        r"(?<![A-Za-z0-9_])([A-Za-z0-9_]*(?:API_KEY|SECRET_KEY|ACCESS_TOKEN|REFRESH_TOKEN|AUTH_TOKEN|PRIVATE_KEY|CLIENT_SECRET|DB_PASSWORD|PASSWORD|PASSWD|SECRET))(\s*[:=]\s*)([\"']?)(?!\[REDACTED:)([^\s\"'`<>}{,\]]{6,})(\3)",
        re.IGNORECASE,
    )
    # 大陆 18 位身份证号：采用前后非字母数字边界（避免中文字符与 \b 在 Python Unicode 正则下失灵），并限 19xx/20xx 出生日期段
    _CN_ID_CARD_RE = re.compile(
        r"(?<![0-9A-Za-z])[1-9]\d{5}((?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))\d{3}[\dXx](?![0-9A-Za-z])"
    )

    # 在代码讨论中常见的占位符或环境变量读取表达式，不应误伤脱敏
    _SAFE_PLACEHOLDER_PREFIXES: tuple[str, ...] = (
        "os.getenv",
        "os.environ",
        "process.env",
        "settings.",
        "${",
        "<your",
        "your_",
        "example_",
        "placeholder",
        "changeme",
    )

    @classmethod
    def sanitize_text(cls, text: str) -> tuple[str, list[str]]:
        """
        对单段文本执行敏感凭据与个人隐私特征扫描脱敏。

        Args:
            text: 待脱敏的原始文本。

        Returns:
            tuple[str, list[str]]: (脱敏后的安全文本, 触发脱敏的类型标签列表)。
        """
        if not text:
            return text, []

        sanitized = text
        redactions: list[str] = []

        def _mark(tag: str) -> None:
            if tag not in redactions:
                redactions.append(tag)

        # 1. PEM 私钥块
        def _replace_pem(match: re.Match[str]) -> str:
            _mark("PRIVATE_KEY")
            return "[REDACTED:PRIVATE_KEY]"

        sanitized = cls._PEM_PRIVATE_KEY_RE.sub(_replace_pem, sanitized)

        # 2. 数据库连接串密码
        def _replace_db_pwd(match: re.Match[str]) -> str:
            if match.group(2).startswith("[REDACTED:"):
                return match.group(0)
            _mark("DB_PASSWORD")
            return f"{match.group(1)}[REDACTED:DB_PASSWORD]{match.group(3)}"

        sanitized = cls._DB_URI_PASSWORD_RE.sub(_replace_db_pwd, sanitized)

        # 3. 各厂商标准前缀 API Key / Token
        def _replace_api_key(_match: re.Match[str]) -> str:
            _mark("API_KEY")
            return "[REDACTED:API_KEY]"

        sanitized = cls._OPENAI_STYLE_KEY_RE.sub(_replace_api_key, sanitized)

        def _replace_google_key(_match: re.Match[str]) -> str:
            _mark("GOOGLE_API_KEY")
            return "[REDACTED:GOOGLE_API_KEY]"

        sanitized = cls._GOOGLE_API_KEY_RE.sub(_replace_google_key, sanitized)

        def _replace_github_token(_match: re.Match[str]) -> str:
            _mark("GITHUB_TOKEN")
            return "[REDACTED:GITHUB_TOKEN]"

        sanitized = cls._GITHUB_TOKEN_RE.sub(_replace_github_token, sanitized)

        def _replace_aws_key(_match: re.Match[str]) -> str:
            _mark("AWS_KEY")
            return "[REDACTED:AWS_KEY]"

        sanitized = cls._AWS_KEY_RE.sub(_replace_aws_key, sanitized)

        # 4. Bearer Token
        def _replace_bearer(match: re.Match[str]) -> str:
            _mark("BEARER_TOKEN")
            return f"{match.group(1)}[REDACTED:BEARER_TOKEN]"

        sanitized = cls._BEARER_TOKEN_RE.sub(_replace_bearer, sanitized)

        # 5. 敏感键值对赋值
        def _replace_kv(match: re.Match[str]) -> str:
            key_name, sep, quote, raw_val, end_quote = match.groups()
            val_lower = raw_val.lower()
            # 跳过纯星号掩码与代码环境变量读取表达，避免误杀工程代码讨论
            if set(raw_val) <= {"*"} or any(val_lower.startswith(p) for p in cls._SAFE_PLACEHOLDER_PREFIXES):
                return match.group(0)
            _mark("SECRET_KV")
            return f"{key_name}{sep}{quote}[REDACTED:SECRET]{end_quote}"

        sanitized = cls._SENSITIVE_KV_RE.sub(_replace_kv, sanitized)

        # 6. 大陆 18 位身份证号（校验出生日期真实合法性，严防 18 位时间戳/ID/Hash 误杀）
        def _replace_id_card(match: re.Match[str]) -> str:
            birth_str = match.group(1)
            try:
                datetime.strptime(birth_str, "%Y%m%d")
            except ValueError:
                return match.group(0)
            _mark("ID_CARD")
            return "[REDACTED:ID_CARD]"

        sanitized = cls._CN_ID_CARD_RE.sub(_replace_id_card, sanitized)

        return sanitized, redactions

    @classmethod
    def sanitize_payload(cls, data: Any) -> tuple[Any, list[str]]:
        """
        递归对结构化对象（dict / list / str）中的敏感字段做安全脱敏。

        Args:
            data: 任意 JSON 可序列化的载荷对象。

        Returns:
            tuple[Any, list[str]]: (脱敏后的对象副本, 触发的脱敏类型列表)。
        """
        all_redactions: list[str] = []

        def _merge(tags: list[str]) -> None:
            for tag in tags:
                if tag not in all_redactions:
                    all_redactions.append(tag)

        def _walk(node: Any) -> Any:
            if isinstance(node, str):
                cleaned, tags = cls.sanitize_text(node)
                _merge(tags)
                return cleaned
            if isinstance(node, list):
                return [_walk(item) for item in node]
            if isinstance(node, tuple):
                return [_walk(item) for item in node]
            if isinstance(node, dict):
                result: dict[str, Any] = {}
                for k, v in node.items():
                    key_str = str(k)
                    key_lower = key_str.lower()
                    # 若键名本身即为高敏凭据字段且值为非空长字符串，直接强制掩码
                    if (
                        isinstance(v, str)
                        and len(v.strip()) >= 6
                        and any(
                            token in key_lower
                            for token in ("api_key", "secret_key", "private_key", "access_token", "refresh_token", "client_secret", "password", "passwd")
                        )
                        and not v.strip().startswith("[REDACTED:")
                        and not (set(v.strip()) <= {"*"})
                    ):
                        _merge(["SECRET_FIELD"])
                        result[key_str] = "[REDACTED:SECRET]"
                    else:
                        result[key_str] = _walk(v)
                return result
            return node

        cleaned_data = _walk(data)
        return cleaned_data, all_redactions

    @classmethod
    def evaluate_write(
        cls,
        source_type: str,
        content: str,
        *,
        incognito: bool = False,
        scene_mode: str = "agent",
        verified: bool = True,
    ) -> MemoryWriteDecision:
        """
        评估一次结构化事实写入请求是否符合准入策略，并返回脱敏后的安全值。

        Args:
            source_type: 写入信号来源标识。
            content: 待写入的事实值或内容文本。
            incognito: 当前会话是否处于无痕模式。
            scene_mode: 当前对话模式（oneshot / chat / agent / plan_first）。
            verified: 当来源为 verified_env 时，标识是否已真正通过执行校验。

        Returns:
            MemoryWriteDecision: 包含准入结论、拒绝原因与脱敏文本。
        """
        if incognito:
            return MemoryWriteDecision(
                allowed=False,
                reason="当前处于无痕模式 (incognito=True)，禁止持久化写入记忆与事件",
            )

        normalized_source = (source_type or "").strip().lower()
        if normalized_source not in cls.ALLOWED_SOURCE_TYPES:
            return MemoryWriteDecision(
                allowed=False,
                reason=f"写入来源 [{source_type}] 不在准入白名单内，严禁模型未验证猜想写入结构化事实库",
            )

        if scene_mode == "plan_first" and normalized_source not in cls.PLAN_FIRST_ALLOWED_SOURCES:
            return MemoryWriteDecision(
                allowed=False,
                reason="Plan-First 只读规划模式下禁止非确认来源自动写入环境事实",
            )

        if normalized_source == "verified_env" and not verified:
            return MemoryWriteDecision(
                allowed=False,
                reason="环境事实未经工具执行验证成功 (verified=False)，拒绝写入",
            )

        sanitized, redactions = cls.sanitize_text(str(content or ""))
        if not sanitized.strip():
            return MemoryWriteDecision(
                allowed=False,
                reason="事实内容为空，忽略写入",
            )

        return MemoryWriteDecision(
            allowed=True,
            reason="准入校验通过",
            sanitized_text=sanitized,
            redactions=redactions,
        )

