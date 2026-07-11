import requests
import json
import os
from tqdm import tqdm

from api.config import INFERENCE_BACKEND, MAX_NEW_TOKENS

import fitz  # PyMuPDF
from PIL import Image
import io
import base64

# First run Popo and Qwen3-VL Locally by vllm

_TRANSFORMERS_MODEL = None
_TRANSFORMERS_PROCESSOR = None
_CURRENT_DEVICE = None


def get_device():
    """自动检测可用的计算设备 (CUDA/MPS/CPU)。
    
    优先级:
    1. 环境变量 POPO_DEVICE_MODE (手动指定)
    2. CUDA (NVIDIA GPU)
    3. MPS (Apple Silicon)
    4. CPU (回退)
    
    Returns:
        str: 设备名称 ("cuda", "mps", 或 "cpu")
    """
    device_mode = os.getenv('POPO_DEVICE_MODE', None)
    if device_mode is not None:
        return device_mode
    
    import torch
    if torch.cuda.is_available():
        return "cuda"
    elif torch.backends.mps.is_available():
        return "mps"
    else:
        return "cpu"


def clean_memory(device=None):
    """清理指定设备的内存缓存。
    
    Args:
        device: 设备名称，如果为 None 则自动检测
    """
    if device is None:
        device = get_device()
    
    import torch
    device_str = str(device)
    
    if device_str.startswith("cuda"):
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    elif device_str.startswith("mps"):
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()


def _get_device():
    """获取当前设备，缓存结果以避免重复检测。"""
    global _CURRENT_DEVICE
    if _CURRENT_DEVICE is None:
        _CURRENT_DEVICE = get_device()
    return _CURRENT_DEVICE


def _transformers_generate(prompt, base64_image):
    global _TRANSFORMERS_MODEL, _TRANSFORMERS_PROCESSOR
    import torch
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

    model_path = os.environ.get(
        "POPO_MODEL_PATH",
        "popo_model",
    )
    max_new_tokens = MAX_NEW_TOKENS
    device = _get_device()

    if _TRANSFORMERS_MODEL is None or _TRANSFORMERS_PROCESSOR is None:
        # 根据设备选择合适的 dtype
        # MPS 不支持 bfloat16，使用 float16 作为替代
        # CPU 上 bfloat16 需要软件模拟（极慢），使用 float32
        if device == "mps":
            torch_dtype = torch.float16
        elif device == "cpu":
            torch_dtype = torch.float32
        else:
            torch_dtype = torch.bfloat16
            
        # Load model directly on the target device
        if device == "mps":
            _TRANSFORMERS_MODEL = Qwen3VLForConditionalGeneration.from_pretrained(
                model_path,
                torch_dtype=torch_dtype,
                device_map="mps",
                low_cpu_mem_usage=True,
            )
        elif device == "cuda":
            _TRANSFORMERS_MODEL = Qwen3VLForConditionalGeneration.from_pretrained(
                model_path,
                torch_dtype=torch_dtype,
                device_map="cuda",
                low_cpu_mem_usage=True,
            )
        else:
            _TRANSFORMERS_MODEL = Qwen3VLForConditionalGeneration.from_pretrained(
                model_path,
                torch_dtype=torch_dtype,
                device_map="cpu",
            )
        _TRANSFORMERS_PROCESSOR = AutoProcessor.from_pretrained(
            model_path,
            tokenizer_kwargs={"padding_side": "left"},
        )
        if hasattr(_TRANSFORMERS_PROCESSOR, "tokenizer") and hasattr(_TRANSFORMERS_PROCESSOR.tokenizer, "padding_side"):
            _TRANSFORMERS_PROCESSOR.tokenizer.padding_side = "left"

    content = []
    if base64_image:
        content.append(
            {
                "type": "image",
                "image": f"data:image/jpeg;base64,{base64_image}",
            }
        )
    content.append({"type": "text", "text": prompt[:100000] if len(prompt) > 100000 else prompt})
    messages = [{"role": "user", "content": content}]

    inputs = _TRANSFORMERS_PROCESSOR.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
    )
    # 将输入转移到模型所在的设备
    device = _TRANSFORMERS_MODEL.device
    inputs = {k: v.to(device) if isinstance(v, torch.Tensor) else v for k, v in inputs.items()}
    
    with torch.no_grad():
        generated_ids = _TRANSFORMERS_MODEL.generate(**inputs, max_new_tokens=max_new_tokens)
    generated_ids_trimmed = [
        out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs["input_ids"], generated_ids)
    ]
    output_text = _TRANSFORMERS_PROCESSOR.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )
    return output_text[0] if output_text else ""

def popo_generate(prompt, base64_image):
    if INFERENCE_BACKEND != "transformers":
        raise RuntimeError(
            "POPO_INFERENCE_BACKEND must be set to 'transformers'. "
            "The Popo model requires fine-tuned weights loaded locally "
            "via transformers, not a generic vLLM/LM Studio endpoint. "
            "Set: export POPO_INFERENCE_BACKEND=transformers"
        )
    return _transformers_generate(prompt, base64_image)

def qwen_generate(prompt, base64_image):
    from openai import OpenAI

    url = ""
    key = ""
    base_model = "Qwen3-VL-4B-Instruct"
    client = OpenAI(
        base_url=url,
        api_key=key
    )
    res = ""
    cnt = 0
    prompt = prompt[:100000] if len(prompt)>100000 else prompt
    
    
    if base64_image:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
    else:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt}
                ],
            }
        ]

    while cnt < 5:
        try:
            response = client.chat.completions.create(
                model=base_model,
                messages=messages,
                max_tokens=50000,
                temperature = 1
            )
            res = response.choices[0].message.content

            return res

        except Exception as e:
            cnt += 1
            print(e)

    return ""

def gpt_generate(prompt, base64_image):#gemini-3-pro-preview
    from openai import OpenAI

    url = ""
    key = ""
    base_model = "gemini-3-flash-preview"
    client = OpenAI(
        base_url=url,
        api_key=key
    )
    res = ""
    cnt = 0
    prompt = prompt[:100000] if len(prompt)>100000 else prompt
    
    
    if base64_image:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
    else:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt}
                ],
            }
        ]

    while cnt < 5:
        try:
            response = client.chat.completions.create(
                model=base_model,
                messages=messages,
                temperature = 1
            )
            res = response.choices[0].message.content

            return res

        except Exception as e:
            cnt += 1
            print(e)

    return ""
