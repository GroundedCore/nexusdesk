import { expect, type APIRequestContext, type Page } from '@playwright/test';

// Full-stack specs run against a real backend, which seeds a default local
// admin (admin / nexusdesk; run the e2e backend with
// AGENT_REQUIRE_PASSWORD_CHANGE=false so the seeded password stays usable).
// Sign in via the API and inject the session so both the SPA (sessionStorage)
// and direct API calls (returned headers) are authenticated.
// Login is rate-limited (10 per user per 15 min), so reuse one session per
// worker process instead of signing in for every test.
let cached: { token: string; headers: Record<string, string> } | null = null;

export async function signInAsAdmin(
  page: Page,
  request: APIRequestContext,
): Promise<Record<string, string>> {
  if (!cached) {
    const response = await request.post('/api/v1/auth/local/login', {
      data: { username: 'admin', password: 'nexusdesk' },
    });
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    cached = {
      token: body.access_token as string,
      headers: { Authorization: `Bearer ${body.access_token}` },
    };
  }
  await page.addInitScript(value => sessionStorage.setItem('agent-platform-token', value), cached!.token);
  return cached!.headers;
}
