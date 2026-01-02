"""Ollama API client for local LLM inference."""

import base64
import io
import json
from typing import Any

import httpx
from loguru import logger
from PIL import Image
from pydantic import BaseModel


class OllamaError(Exception):
    """Error communicating with Ollama."""

    pass


class OllamaClient:
    """Client for Ollama local LLM API."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 11434,
        model: str = "mistral",
        timeout: float = 300.0,
    ):
        self.base_url = f"http://{host}:{port}"
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.1,
        format_json: bool = False,
        schema: dict | None = None,
    ) -> str:
        """Generate a completion from the model.

        Args:
            prompt: The user prompt
            system: Optional system prompt
            temperature: Sampling temperature (lower = more deterministic)
            format_json: Whether to request JSON output format
            schema: Optional JSON schema for structured output

        Returns:
            The model's response text
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": 4096,  # Enough for ~100 transactions
                "num_ctx": 16384,     # Balanced context window
            },
        }

        if system:
            payload["system"] = system

        # Schema takes precedence over format_json
        if schema:
            payload["format"] = schema
        elif format_json:
            payload["format"] = "json"

        logger.debug(
            f"Ollama request: model={self.model}, prompt_len={len(prompt)}, "
            f"system_len={len(system) if system else 0}, schema={'yes' if schema else 'no'}"
        )

        try:
            response = self._client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error: {e.response.status_code}")
            raise OllamaError(f"Ollama HTTP error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Ollama connection error: {e}")
            raise OllamaError(f"Failed to connect to Ollama: {e}") from e

        data = response.json()
        result = data.get("response", "")

        # Timing breakdown (all in nanoseconds from Ollama)
        total_ns = data.get("total_duration", 0)
        load_ns = data.get("load_duration", 0)
        prompt_eval_ns = data.get("prompt_eval_duration", 0)
        eval_ns = data.get("eval_duration", 0)

        logger.debug(
            f"Ollama response: len={len(result)}, "
            f"prompt_tokens={data.get('prompt_eval_count')}, eval_tokens={data.get('eval_count')}, "
            f"total={total_ns/1e9:.2f}s (prompt={prompt_eval_ns/1e9:.2f}s, eval={eval_ns/1e9:.2f}s, load={load_ns/1e9:.2f}s)"
        )

        return result

    def generate_structured[T: BaseModel](
        self,
        prompt: str,
        response_model: type[T],
        system: str | None = None,
        temperature: float = 0.1,
    ) -> T:
        """Generate a structured response matching a Pydantic model.

        Args:
            prompt: The user prompt
            response_model: Pydantic model class for the response
            system: Optional system prompt
            temperature: Sampling temperature

        Returns:
            Validated Pydantic model instance
        """
        # Get JSON schema from Pydantic model
        schema = response_model.model_json_schema()

        response = self.generate(
            prompt=prompt,
            system=system,
            temperature=temperature,
            schema=schema,
        )

        try:
            data = json.loads(response)
            return response_model.model_validate(data)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}, response preview: {response[:200]}")
            raise OllamaError(f"Failed to parse JSON response: {e}") from e
        except Exception as e:
            logger.error(f"Validation error: {e}, response preview: {response[:200]}")
            raise OllamaError(f"Failed to validate response: {e}") from e

    def generate_json(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> dict | list:
        """Generate a JSON response from the model.

        Args:
            prompt: The user prompt
            system: Optional system prompt
            temperature: Sampling temperature

        Returns:
            Parsed JSON response
        """
        response = self.generate(
            prompt=prompt,
            system=system,
            temperature=temperature,
            format_json=True,
        )

        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}, response preview: {response[:200]}")
            raise OllamaError(f"Failed to parse JSON response: {e}") from e

    def generate_vision(
        self,
        prompt: str,
        image: Image.Image,
        system: str | None = None,
        temperature: float = 0.1,
        format_json: bool = False,
    ) -> str:
        """Generate a completion from a vision model with an image.

        Args:
            prompt: The user prompt
            image: PIL Image to analyze
            system: Optional system prompt
            temperature: Sampling temperature
            format_json: Whether to request JSON output format

        Returns:
            The model's response text
        """
        # Convert PIL image to base64
        if image.mode != "RGB":
            image = image.convert("RGB")

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "images": [image_base64],
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }

        if system:
            payload["system"] = system

        if format_json:
            payload["format"] = "json"

        logger.debug(
            f"Ollama vision request: model={self.model}, prompt_len={len(prompt)}, image_size={image.size}"
        )

        try:
            response = self._client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error: {e.response.status_code}")
            raise OllamaError(f"Ollama HTTP error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Ollama connection error: {e}")
            raise OllamaError(f"Failed to connect to Ollama: {e}") from e

        data = response.json()
        result = data.get("response", "")

        # Timing breakdown (all in nanoseconds from Ollama)
        total_ns = data.get("total_duration", 0)
        load_ns = data.get("load_duration", 0)
        prompt_eval_ns = data.get("prompt_eval_duration", 0)
        eval_ns = data.get("eval_duration", 0)

        logger.debug(
            f"Ollama vision response: len={len(result)}, "
            f"prompt_tokens={data.get('prompt_eval_count')}, eval_tokens={data.get('eval_count')}, "
            f"total={total_ns/1e9:.2f}s (prompt={prompt_eval_ns/1e9:.2f}s, eval={eval_ns/1e9:.2f}s, load={load_ns/1e9:.2f}s)"
        )

        return result

    def generate_vision_json(
        self,
        prompt: str,
        image: Image.Image,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> dict | list:
        """Generate a JSON response from a vision model.

        Args:
            prompt: The user prompt
            image: PIL Image to analyze
            system: Optional system prompt
            temperature: Sampling temperature

        Returns:
            Parsed JSON response
        """
        response = self.generate_vision(
            prompt=prompt,
            image=image,
            system=system,
            temperature=temperature,
            format_json=True,
        )

        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}, response preview: {response[:200]}")
            raise OllamaError(f"Failed to parse JSON response: {e}") from e

    def check_connection(self) -> bool:
        """Check if Ollama is running and accessible."""
        try:
            response = self._client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except httpx.RequestError:
            return False

    def check_model(self) -> bool:
        """Check if the configured model is available."""
        try:
            response = self._client.get(f"{self.base_url}/api/tags")
            if response.status_code != 200:
                return False
            data = response.json()
            models = [m["name"].split(":")[0] for m in data.get("models", [])]
            return self.model in models or f"{self.model}:latest" in [
                m["name"] for m in data.get("models", [])
            ]
        except (httpx.RequestError, KeyError):
            return False

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()
