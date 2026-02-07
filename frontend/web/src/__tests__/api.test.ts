import { api } from "../lib/api";

const defaultBase = "http://localhost:8000/api/v1";

describe("api helper", () => {
  beforeEach(() => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      headers: { get: () => "application/json" },
      json: async () => ({ ok: true }),
      text: async () => '{"ok":true}',
    } as any);
  });

  it("prefixes get requests with API base", async () => {
    await api.get("/health");
    expect(fetch).toHaveBeenCalledWith(`${defaultBase}/health`, expect.any(Object));
  });

  it("posts JSON bodies with headers set", async () => {
    const body = { foo: "bar" };
    await api.post("/chat", body);
    const [, options] = (fetch as jest.Mock).mock.calls[0];
    expect((fetch as jest.Mock).mock.calls[0][0]).toBe(`${defaultBase}/chat`);
    expect(options.method).toBe("POST");
    expect(options.headers["Content-Type"]).toBe("application/json");
    expect(options.body).toBe(JSON.stringify(body));
  });
});
