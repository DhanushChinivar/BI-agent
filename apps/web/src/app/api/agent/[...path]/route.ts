/**
 * BFF catch-all proxy — forwards all /api/agent/* requests to the FastAPI agent.
 */
import { auth } from "@clerk/nextjs/server";
import { type NextRequest } from "next/server";

const AGENT_URL = process.env.AGENT_URL ?? "http://localhost:8000";
const IS_SSE_PATH = (path: string) => path.includes("query/stream");

async function proxy(req: NextRequest, segments: string[]): Promise<Response> {
  const path = segments.join("/");
  const search = req.nextUrl.search;
  const url = `${AGENT_URL}/${path}${search}`;

  const headers = new Headers(req.headers);
  headers.delete("host");

  // Inject Clerk JWT if available; otherwise fall back to dev-user for local dev
  try {
    const { getToken, userId } = await auth();
    const token = await getToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    } else if (userId) {
      headers.set("X-User-Id", userId);
    } else {
      headers.set("X-User-Id", "dev-user");
    }
  } catch {
    headers.set("X-User-Id", "dev-user");
  }

  const upstream = await fetch(url, {
    method: req.method,
    headers,
    body: req.method !== "GET" && req.method !== "HEAD" ? req.body : undefined,
    duplex: "half",
    redirect: "manual",
  } as RequestInit);

  // For SSE endpoints, set explicit no-buffer headers so chunks reach the browser
  const responseHeaders = new Headers(upstream.headers);
  if (IS_SSE_PATH(path)) {
    responseHeaders.set("Content-Type", "text/event-stream");
    responseHeaders.set("Cache-Control", "no-cache, no-transform");
    responseHeaders.set("Connection", "keep-alive");
    responseHeaders.set("X-Accel-Buffering", "no");
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(req, path);
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(req, path);
}

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(req, path);
}
