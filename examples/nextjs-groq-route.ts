/**
 * Next.js App Router용 Groq API Route 예시.
 * 프로젝트에 Next.js를 추가할 때 app/api/groq/route.ts 로 복사해서 사용.
 */

import OpenAI from "openai";
import { NextResponse } from "next/server";

const client = new OpenAI({
  apiKey: process.env.GROQ_API_KEY,
  baseURL: "https://api.groq.com/openai/v1",
});

export async function POST(req: Request) {
  const { prompt } = await req.json();

  const response = await client.chat.completions.create({
    model: "llama-3.1-8b-instant",
    messages: [
      {
        role: "system",
        content: "너는 한국어 블로그 제목과 키워드를 잘 뽑는 도우미야.",
      },
      {
        role: "user",
        content: prompt,
      },
    ],
  });

  return NextResponse.json({
    result: response.choices[0].message.content,
  });
}
