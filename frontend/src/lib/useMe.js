import { useEffect, useState } from "react";
import { api, getToken } from "./api.js";

// The signed-in user is needed by the layout and by every route guard. Cache the
// in-flight request so a page load issues one /auth/me call rather than one per
// consumer.
let cached = null;
let inFlight = null;

export function clearMeCache() {
  cached = null;
  inFlight = null;
}

function fetchMe() {
  if (cached) return Promise.resolve(cached);
  if (!inFlight) {
    inFlight = api("/auth/me")
      .then((user) => {
        cached = user;
        return user;
      })
      .finally(() => {
        inFlight = null;
      });
  }
  return inFlight;
}

export function useMe() {
  const [me, setMe] = useState(cached);
  const [loading, setLoading] = useState(!cached && Boolean(getToken()));

  useEffect(() => {
    let active = true;
    if (cached || !getToken()) {
      setLoading(false);
      return undefined;
    }
    fetchMe()
      .then((user) => {
        if (active) setMe(user);
      })
      .catch(() => {
        if (active) setMe(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return { me, loading };
}
