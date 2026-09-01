// Public config for the frontend. The Supabase anon key is DESIGNED to be
// public/client-side (Supabase's Row Level Security restricts it to read-only
// SELECT queries -- see supabase/schema.sql). Never put the service_role key here.
const CONFIG = {
  SUPABASE_URL: "https://sehiookymtopjfeiyrti.supabase.co",
  SUPABASE_ANON_KEY: "sb_publishable_bpeF8rAepOdVneeCQN8ozg_UAhGRVm5",
};
