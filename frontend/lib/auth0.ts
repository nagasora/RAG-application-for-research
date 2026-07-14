"use client";

import { createAuth0Client, type Auth0Client } from "@auth0/auth0-spa-js";

import { ApiError } from "@/lib/api/error";

type Auth0Configuration = {
  domain: string;
  clientId: string;
  audience: string;
};

let clientPromise: Promise<Auth0Client> | null = null;

function auth0Configuration(): Auth0Configuration {
  const domain = process.env.NEXT_PUBLIC_AUTH0_DOMAIN?.trim();
  const clientId = process.env.NEXT_PUBLIC_AUTH0_CLIENT_ID?.trim();
  const audience = process.env.NEXT_PUBLIC_AUTH0_AUDIENCE?.trim();
  if (!domain || !clientId || !audience) {
    throw new ApiError("Auth0 の公開設定が不足しています。NEXT_PUBLIC_AUTH0_DOMAIN、NEXT_PUBLIC_AUTH0_CLIENT_ID、NEXT_PUBLIC_AUTH0_AUDIENCE を設定してください。", {
      status: 503,
      code: "auth_configuration_error",
    });
  }
  return { domain, clientId, audience };
}

function auth0Client(): Promise<Auth0Client> {
  if (typeof window === "undefined") {
    throw new ApiError("Auth0 はブラウザ上で初期化してください", { status: 503, code: "auth_configuration_error" });
  }
  if (!clientPromise) {
    const { domain, clientId, audience } = auth0Configuration();
    clientPromise = createAuth0Client({
      domain,
      clientId,
      authorizationParams: {
        audience,
        redirect_uri: window.location.origin,
      },
      // API トークンを永続ストレージへ保存せず、現在のタブだけで利用する。
      cacheLocation: "memory",
    }).then(async client => {
      const callbackUrl = new URL(window.location.href);
      if (callbackUrl.searchParams.has("code") && callbackUrl.searchParams.has("state")) {
        await client.handleRedirectCallback();
        callbackUrl.searchParams.delete("code");
        callbackUrl.searchParams.delete("state");
        callbackUrl.searchParams.delete("error");
        callbackUrl.searchParams.delete("error_description");
        window.history.replaceState({}, document.title, `${callbackUrl.pathname}${callbackUrl.search}${callbackUrl.hash}`);
      }
      return client;
    });
  }
  return clientPromise!;
}

export async function getAuth0AccessToken(): Promise<string> {
  const client = await auth0Client();
  if (!await client.isAuthenticated()) {
    throw new ApiError("ログインが必要です", { status: 401, code: "missing_access_token" });
  }
  const token = await client.getTokenSilently();
  if (!token) throw new ApiError("Auth0 からアクセストークンを取得できませんでした", { status: 401, code: "missing_access_token" });
  return token;
}

export async function loginWithAuth0(): Promise<void> {
  const client = await auth0Client();
  const { audience } = auth0Configuration();
  await client.loginWithRedirect({ authorizationParams: { audience } });
}

export async function logoutFromAuth0(): Promise<void> {
  const client = await auth0Client();
  await client.logout({ logoutParams: { returnTo: window.location.origin } });
}
