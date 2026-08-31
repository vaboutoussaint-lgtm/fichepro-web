-- ═══════════════════════════════════════════════════════
--  FichePro — Script SQL Supabase
--  Collez ce script dans : Supabase > SQL Editor > Run
-- ═══════════════════════════════════════════════════════

-- 1. Table LICENCES
create table if not exists licenses (
  id           uuid primary key default gen_random_uuid(),
  license_key  text unique not null,
  client_name  text not null,
  expiry_date  date,
  created_at   timestamptz default now()
);

-- 2. Table PRODUCTEURS
create table if not exists producers (
  id          uuid primary key default gen_random_uuid(),
  license_key text not null references licenses(license_key) on delete cascade,
  code        text,
  nom         text not null,
  section     text default 'A',
  surface_ha  float default 0,
  telephone   text,
  created_at  timestamptz default now()
);

create index if not exists idx_producers_license on producers(license_key);
create index if not exists idx_producers_section on producers(section);

-- 3. Table FICHES
create table if not exists fiches (
  id               uuid primary key default gen_random_uuid(),
  license_key      text not null,
  producer_id      uuid references producers(id) on delete set null,
  numero_fiche     text,
  date_fiche       date,
  poids_brut       float default 0,
  impurete         float default 0,
  poids_net        float default 0,
  prix_unitaire    float default 0,
  montant_total    float default 0,
  sections_volumes jsonb,
  calendrier       text,
  notes            text,
  created_at       timestamptz default now()
);

create index if not exists idx_fiches_license   on fiches(license_key);
create index if not exists idx_fiches_producer  on fiches(producer_id);
create index if not exists idx_fiches_date      on fiches(date_fiche);

-- ═══════════════════════════════════════════════════════
--  Insérer vos licences clients ici :
-- ═══════════════════════════════════════════════════════

-- Exemple : licence legacy de test
insert into licenses (license_key, client_name, expiry_date)
values ('WIL03943487HAY', 'WILFRIED - Admin', '2027-12-31')
on conflict (license_key) do nothing;

-- Ajouter un nouveau client :
-- insert into licenses (license_key, client_name, expiry_date)
-- values ('CLI-VOTRE-CLE-ICI', 'Nom du Client', '2026-12-31');

-- ═══════════════════════════════════════════════════════
--  RLS (Row Level Security) — données isolées par client
-- ═══════════════════════════════════════════════════════

alter table licenses  enable row level security;
alter table producers enable row level security;
alter table fiches    enable row level security;

-- Pour la clé de service (backend), tout est accessible
-- (la clé SUPABASE_KEY dans les variables Render doit être
--  la clé "service_role", pas la clé "anon")
