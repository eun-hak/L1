import { config } from "dotenv";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";
import { groqChat, GROQ_MODELS } from "../lib/groq/client.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
config({ path: resolve(root, ".env") });
config({ path: resolve(root, ".env.local") });

const prompt =
  process.argv.slice(2).join(" ") ||
  "씨제이택배배송조회 관련 블로그 제목 10개 추천해줘";

const result = await groqChat({
  prompt,
  model: GROQ_MODELS.fast,
});

console.log(result);
