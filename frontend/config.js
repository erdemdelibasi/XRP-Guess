// Public config for the frontend. The Supabase anon key is DESIGNED to be
// public/client-side (Supabase's Row Level Security restricts it to read-only
// SELECT queries -- see supabase/schema.sql). Never put the service_role key here.
const CONFIG = {
  SUPABASE_URL: "https://sehiookymtopjfeiyrti.supabase.co",
  SUPABASE_ANON_KEY: "sb_publishable_bpeF8rAepOdVneeCQN8ozg_UAhGRVm5",
  // SHA-256 hex hash of your chosen password. See README for how to generate it.
  PASSWORD_HASH: "5b235799af2c8f6bdbf761ccee4c07822bae6f667ac0ecd80c598036e11effbe",
};
