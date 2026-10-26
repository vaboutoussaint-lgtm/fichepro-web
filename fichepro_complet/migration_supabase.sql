-- ============================================================
-- FichePro v4 - Migration Supabase
-- ============================================================

-- ============================================================
-- 1. CONFIG (parametres campagne, saison, prix)
-- ============================================================
CREATE TABLE IF NOT EXISTS config (
    cle         TEXT PRIMARY KEY,
    valeur      TEXT NOT NULL,
    description TEXT
);

INSERT INTO config (cle, valeur, description) VALUES
    ('COOP_NOM',           '[NOM_COOP]',         'Nom de la cooperative'),
    ('COOP_ADRESSE',       '',                 'Adresse'),
    ('COOP_TELEPHONE',     '',                 'Telephone'),
    ('COOP_FAX',           '',                 'Fax'),
    ('ANNEE_CAMPAGNE',     '2024-2025',        'Campagne en cours'),
    ('SAISON_EN_COURS',    'GT',               'GT ou PT'),
    ('DATE_DEBUT_GT',      '2024-10-01',       'Date debut grande traite'),
    ('DATE_FIN_GT',        '2025-03-31',       'Date fin grande traite'),
    ('DATE_DEBUT_PT',      '2025-04-01',       'Date debut petite traite'),
    ('DATE_FIN_PT',        '2025-09-30',       'Date fin petite traite'),
    ('QUOTA_GT',           '0.80',             'Quota grande traite 80%'),
    ('PRIX_KG_GT',         '0',                'Prix kg grande traite (FCFA)'),
    ('PRIX_KG_PT',         '0',                'Prix kg petite traite (FCFA)'),
    ('SCORE_UTILISATION',  '0.50',             'Poids score taux non utilise'),
    ('SCORE_VOLUME',       '0.30',             'Poids score volume restant'),
    ('SCORE_DISPO',        '0.20',             'Poids score disponibilite'),
    ('PLAFOND_PARCELLE',   '0.30',             'Plafond 30% par parcelle par fiche'),
    ('INTERV_JOURS',       '14',               'Intervalle minimum entre livraisons (jours)'),
    ('POIDS_MIN',          '25',               'Poids minimum par ligne (kg)'),
    ('POIDS_SAC_KG',       '75',               'Poids par sac (kg)')
ON CONFLICT (cle) DO NOTHING;

-- ============================================================
-- 2. UTILISATEURS (code acces + profil)
-- ============================================================
CREATE TABLE IF NOT EXISTS utilisateurs (
    id          SERIAL PRIMARY KEY,
    code_acces  TEXT UNIQUE NOT NULL,
    profil      TEXT NOT NULL CHECK (profil IN ('DIRECTEUR', 'TRACABILITE')),
    actif       BOOLEAN DEFAULT TRUE
);

-- Utilisateurs par defaut (codes a changer en production)
INSERT INTO utilisateurs (code_acces, profil) VALUES
    ('DIR2024',   'DIRECTEUR'),
    ('TRACE2024', 'TRACABILITE')
ON CONFLICT (code_acces) DO NOTHING;

-- ============================================================
-- 3. SESSIONS (connexions journalisees)
-- ============================================================
CREATE TABLE IF NOT EXISTS sessions (
    id              SERIAL PRIMARY KEY,
    nom_agent       TEXT NOT NULL,
    profil          TEXT NOT NULL,
    date_connexion  TIMESTAMPTZ DEFAULT NOW(),
    date_deconnexion TIMESTAMPTZ,
    token           TEXT UNIQUE NOT NULL
);

-- ============================================================
-- 4. PARCELLES (registre importe)
-- ============================================================
CREATE TABLE IF NOT EXISTS parcelles (
    code_parcelle       TEXT PRIMARY KEY,
    code_producteur     TEXT NOT NULL,
    nom_producteur      TEXT NOT NULL,
    section             TEXT,
    village             TEXT,
    superficie_ha       DOUBLE PRECISION DEFAULT 0,
    estimation_kg       DOUBLE PRECISION DEFAULT 0,
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    rainforest_alliance BOOLEAN DEFAULT FALSE,
    fairtrade           BOOLEAN DEFAULT FALSE,
    actif               BOOLEAN DEFAULT TRUE,
    -- Volumes livres par saison (mis a jour apres chaque repartition)
    volume_livre_gt     DOUBLE PRECISION DEFAULT 0,
    volume_livre_pt     DOUBLE PRECISION DEFAULT 0,
    -- Reserve interne (quota planifie non encore livre en caisse)
    reserve_gt          DOUBLE PRECISION DEFAULT 0,
    reserve_pt          DOUBLE PRECISION DEFAULT 0,
    -- Residus GT reportes sur PT
    report_gt_vers_pt   DOUBLE PRECISION DEFAULT 0,
    date_import         TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 5. FICHES DE REPARTITION
-- ============================================================
CREATE TABLE IF NOT EXISTS fiches_repartition (
    num_fiche           TEXT PRIMARY KEY,
    date_creation       TIMESTAMPTZ DEFAULT NOW(),
    saison              TEXT NOT NULL CHECK (saison IN ('GT', 'PT')),
    label               TEXT NOT NULL CHECK (label IN ('RA', 'FT')),
    volume_total        DOUBLE PRECISION NOT NULL,
    volume_affecte      DOUBLE PRECISION NOT NULL,
    nombre_sacs         INTEGER DEFAULT 0,
    plafond_applique    DOUBLE PRECISION DEFAULT 0.30,
    intervalle_saute    BOOLEAN DEFAULT FALSE,
    type_repartition    TEXT DEFAULT 'INTERNE' CHECK (type_repartition IN ('INTERNE', 'COOP')),
    nom_agent           TEXT NOT NULL,
    sections            TEXT,
    statut              TEXT DEFAULT 'CLOTUREE'
);

-- ============================================================
-- 6. LIGNES DE REPARTITION
-- ============================================================
CREATE TABLE IF NOT EXISTS lignes_repartition (
    id                  SERIAL PRIMARY KEY,
    num_fiche           TEXT NOT NULL REFERENCES fiches_repartition(num_fiche),
    code_parcelle       TEXT NOT NULL,
    code_producteur     TEXT NOT NULL,
    nom_producteur      TEXT NOT NULL,
    section             TEXT,
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    score_calcule       DOUBLE PRECISION,
    volume_attribue     DOUBLE PRECISION NOT NULL,
    nombre_sacs         INTEGER DEFAULT 0,
    montant             DOUBLE PRECISION DEFAULT 0,
    date_livraison      DATE,
    statut              TEXT DEFAULT 'PLANIFIE'
);

-- ============================================================
-- 7. FICHES D'ACCOMPAGNEMENT
-- ============================================================
CREATE TABLE IF NOT EXISTS fiches_accompagnement (
    id                  SERIAL PRIMARY KEY,
    num_fiche_rep       TEXT NOT NULL REFERENCES fiches_repartition(num_fiche),
    date_creation       TIMESTAMPTZ DEFAULT NOW(),
    label               TEXT NOT NULL CHECK (label IN ('RA', 'FT')),
    saison              TEXT NOT NULL,
    campagne            TEXT NOT NULL,
    -- Infos chargement (saisie manuelle)
    num_tracteur        TEXT,
    num_remorque        TEXT,
    nom_chauffeur       TEXT,
    num_connaissement_ccc TEXT,
    num_lot             TEXT,  -- = num_fiche_rep
    volume_total        DOUBLE PRECISION,
    nombre_sacs_total   INTEGER,
    nom_agent           TEXT NOT NULL
);

-- ============================================================
-- 8. DEROGATIONS (intervalle 14j ou plafond 30%)
-- ============================================================
CREATE TABLE IF NOT EXISTS derogations (
    id              SERIAL PRIMARY KEY,
    num_fiche       TEXT NOT NULL,
    type_derogation TEXT NOT NULL CHECK (type_derogation IN ('INTERVALLE', 'PLAFOND')),
    valeur_avant    TEXT,
    valeur_apres    TEXT,
    nom_agent       TEXT NOT NULL,
    date_heure      TIMESTAMPTZ DEFAULT NOW(),
    motif           TEXT NOT NULL
);

-- ============================================================
-- 9. CLOTURE GT -> PT (report residus)
-- ============================================================
CREATE TABLE IF NOT EXISTS clotures_saison (
    id              SERIAL PRIMARY KEY,
    saison_cloturee TEXT NOT NULL,
    campagne        TEXT NOT NULL,
    date_cloture    TIMESTAMPTZ DEFAULT NOW(),
    nom_agent       TEXT NOT NULL,
    description     TEXT NOT NULL,
    nb_parcelles    INTEGER DEFAULT 0,
    volume_reporte  DOUBLE PRECISION DEFAULT 0
);

-- ============================================================
-- 10. JOURNAL (toutes actions tracees)
-- ============================================================
CREATE TABLE IF NOT EXISTS journal (
    id          SERIAL PRIMARY KEY,
    date_heure  TIMESTAMPTZ DEFAULT NOW(),
    nom_agent   TEXT NOT NULL,
    profil      TEXT,
    action      TEXT NOT NULL,
    details     TEXT,
    reference   TEXT
);

-- ============================================================
-- INDEX
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_parcelles_producteur ON parcelles(code_producteur);
CREATE INDEX IF NOT EXISTS idx_parcelles_ra ON parcelles(rainforest_alliance) WHERE rainforest_alliance = TRUE;
CREATE INDEX IF NOT EXISTS idx_parcelles_ft ON parcelles(fairtrade) WHERE fairtrade = TRUE;
CREATE INDEX IF NOT EXISTS idx_lignes_fiche ON lignes_repartition(num_fiche);
CREATE INDEX IF NOT EXISTS idx_lignes_parcelle ON lignes_repartition(code_parcelle);
CREATE INDEX IF NOT EXISTS idx_journal_date ON journal(date_heure DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
