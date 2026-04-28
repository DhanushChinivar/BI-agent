/**
 * BFF catch-all proxy — forwards all /api/agent/* requests to the FastAPI agent.
 * Injects the Clerk JWT as Authorization: Bearer <token> so FastAPI can verify identity.
 */
import { auth } from "@clerk/nextjs/server";
import { type NextRequest } from "next/server";

const AGENT_URL = process.env.AGENT_URL ?? "http://localhost:8000";

async function proxy(req: NextRequest, segments: string[]): Promise<Response> {
  const path = segments.join("/");
  const search = req.nextUrl.search;
  const url = `${AGENT_URL}/${path}${search}`;

  const { getToken } = await auth();
  const token = await getToken();

  const headers = new Headers(req.headers);
  headers.delete("host");
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const upstream = await fetch(url, {
    method: req.method,
    headers,
    body: req.method !== "GET" && req.method !== "HEAD" ? req.body : undefined,
    duplex: "half",
  } as RequestInit);

  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstream.headers,
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
