"""The tool-call parser must match the format each model family emits.

A wrong parser does not error; it silently leaves tool calls unparsed in the
reply text, so the agent chats but never executes a tool. These tests pin the
family mapping and its precedence rules.
"""

from backend.app.inference.vllm_runtime import tool_call_parser


def test_qwen_and_unknown_families_default_to_hermes():
    assert tool_call_parser("mlx-community/Qwen3-0.6B-4bit") == "hermes"
    assert tool_call_parser("Qwen/Qwen2.5-7B-Instruct") == "hermes"
    assert tool_call_parser("some-org/some-model") == "hermes"


def test_qwen3_coder_gets_its_own_parser():
    assert tool_call_parser("mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit") == "qwen3_coder"


def test_gpt_oss_uses_the_openai_parser():
    assert tool_call_parser("mlx-community/gpt-oss-20b-MXFP4-Q8") == "openai"


def test_llama_versions_are_distinguished():
    assert tool_call_parser("mlx-community/Llama-3.2-3B-Instruct-4bit") == "llama3_json"
    # Llama 4 emits pythonic tool calls, not the Llama 3 JSON format.
    assert tool_call_parser("mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit") == "llama4_pythonic"


def test_deepseek_versions_are_distinguished():
    assert tool_call_parser("mlx-community/DeepSeek-V3-0324-4bit") == "deepseek_v3"
    assert tool_call_parser("deepseek-ai/DeepSeek-V3.1-Base") == "deepseek_v31"
    # R1 distills keep the template of the model they were distilled into.
    assert tool_call_parser("mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit") == "hermes"
    assert tool_call_parser("mlx-community/DeepSeek-R1-Distill-Llama-8B-4bit") == "llama3_json"


def test_granite_versions_are_distinguished():
    assert tool_call_parser("ibm-granite/granite-3.3-8b-instruct") == "granite"
    assert tool_call_parser("ibm-granite/granite-4.0-h-small") == "granite4"
    assert tool_call_parser("ibm-granite/granite-20b-functioncalling") != "granite4"


def test_remaining_families():
    assert tool_call_parser("mlx-community/Mistral-7B-Instruct-v0.3-4bit") == "mistral"
    assert tool_call_parser("mlx-community/GLM-4.5-Air-4bit") == "glm45"
    assert tool_call_parser("mlx-community/Phi-4-mini-instruct-4bit") == "phi4_mini_json"
    assert tool_call_parser("internlm/internlm2_5-7b-chat") == "internlm"
    assert tool_call_parser("mlx-community/Kimi-K2-Instruct-4bit") == "kimi_k2"


def test_matching_is_case_insensitive():
    assert tool_call_parser("MLX-Community/LLAMA-3.2-3B-4bit") == "llama3_json"
