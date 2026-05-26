import OpenAI from "openai";

export const GROQ_BASE_URL = "https://api.groq.com/openai/v1";

/** @typedef {"llama-3.1-8b-instant" | "qwen/qwen3-32b" | "llama-3.3-70b-versatile" | "meta-llama/llama-4-scout-17b-16e-instruct"} GroqModel */

export const GROQ_MODELS = {
  /** 대량 제목/키워드 생성 */
  fast: "llama-3.1-8b-instant",
  /** 본문 초안/요약 */
  draft: "qwen/qwen3-32b",
  /** 품질 좋은 글 생성 */
  quality: "llama-3.3-70b-versatile",
  /** 긴 문서 처리 */
  long: "meta-llama/llama-4-scout-17b-16e-instruct",
};

/**
 * @param {string | undefined} apiKey
 * @returns {OpenAI}
 */
export function createGroqClient(apiKey = process.env.GROQ_API_KEY ?? process.env.GROK_API_KEY) {
  if (!apiKey) {
    throw new Error("GROQ_API_KEY 환경변수가 필요합니다.");
  }

  return new OpenAI({
    apiKey,
    baseURL: GROQ_BASE_URL,
  });
}

/**
 * @param {object} options
 * @param {string} options.prompt
 * @param {string} [options.system]
 * @param {GroqModel} [options.model]
 * @param {number} [options.temperature]
 * @returns {Promise<string>}
 */
export async function groqChat({
  prompt,
  system = "너는 한국어 블로그 제목과 키워드를 잘 뽑는 도우미야.",
  model = GROQ_MODELS.fast,
  temperature = 0.7,
}) {
  const client = createGroqClient();
  const response = await client.chat.completions.create({
    model,
    messages: [
      { role: "system", content: system },
      { role: "user", content: prompt },
    ],
    temperature,
  });

  return response.choices[0]?.message?.content ?? "";
}
