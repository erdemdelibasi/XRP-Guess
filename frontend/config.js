// Public config for the frontend. The Supabase anon key is DESIGNED to be
// public/client-side (Supabase's Row Level Security restricts it to read-only
// SELECT queries -- see supabase/schema.sql). Never put the service_role key here.
const CONFIG = {
  SUPABASE_URL: "https://YOUR-PROJECT-REF.supabase.co",
  SUPABASE_ANON_KEY: "YOUR-ANON-PUBLIC-KEY",
  // SHA-256 hex hash of your chosen password. See README for how to generate it.
  PASSWORD_HASH: "REPLACE-WITH-YOUR-SHA256-HASH",
};
