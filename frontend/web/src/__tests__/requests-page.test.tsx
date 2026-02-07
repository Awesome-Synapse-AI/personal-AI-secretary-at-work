/**
 * Verifies that the Requests dashboard calls the expected backend endpoints.
 */
"use client";

import "@testing-library/jest-dom";
import { render, waitFor } from "@testing-library/react";
import RequestsPage from "../app/requests/page";

jest.mock("@/lib/config", () => ({
  API_BASE_URL: "http://localhost:8000/api/v1",
  WS_BASE_URL: "ws://localhost:8000/api/v1",
  APPROVALS_WS_URL: "",
  DEFAULT_TENANT: "default",
  DEMO_USER_ID: "demo-user",
}));

function mockFetchSequence() {
  const payloads = [
    { requests: [] },
    { expenses: [] },
    { travel_requests: [] },
    { access_requests: [] },
    { tickets: [] },
  ];
  (global.fetch as jest.Mock) = jest.fn().mockImplementation(() => {
    const next = payloads.shift() ?? {};
    return Promise.resolve({
      ok: true,
      headers: { get: () => "application/json" },
      json: async () => next,
      text: async () => JSON.stringify(next),
    });
  });
}

describe("RequestsPage data fetching", () => {
  beforeEach(() => {
    mockFetchSequence();
    // minimal WebSocket mock to avoid runtime errors if used
    (global as any).WebSocket = class {
      constructor() {}
      close() {}
    };
  });

  it("calls all domain endpoints once on load", async () => {
    render(<RequestsPage />);

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(5));

    const calledUrls = (fetch as jest.Mock).mock.calls.map((c: any[]) => c[0]);
    expect(calledUrls).toEqual(
      expect.arrayContaining([
        "http://localhost:8000/api/v1/domain/requests/me",
        "http://localhost:8000/api/v1/domain/expenses/me",
        "http://localhost:8000/api/v1/domain/travel-requests/me",
        "http://localhost:8000/api/v1/domain/access-requests/me",
        "http://localhost:8000/api/v1/domain/tickets/me",
      ])
    );
  });
});
