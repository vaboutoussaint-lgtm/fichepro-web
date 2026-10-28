-- ============================================================
-- FIX RLS : désactiver Row Level Security sur toutes les tables
-- applicatives (la sécurité est gérée par l'API Flask, pas Supabase)
-- À exécuter dans Supabase > SQL Editor
-- ============================================================

ALTER TABLE parcelles              DISABLE ROW LEVEL SECURITY;
ALTER TABLE fiches_repartition     DISABLE ROW LEVEL SECURITY;
ALTER TABLE lignes_repartition     DISABLE ROW LEVEL SECURITY;
ALTER TABLE fiches_accompagnement  DISABLE ROW LEVEL SECURITY;
ALTER TABLE derogations            DISABLE ROW LEVEL SECURITY;
ALTER TABLE clotures_saison        DISABLE ROW LEVEL SECURITY;
ALTER TABLE journal                DISABLE ROW LEVEL SECURITY;
ALTER TABLE sessions               DISABLE ROW LEVEL SECURITY;
ALTER TABLE config                 DISABLE ROW LEVEL SECURITY;
ALTER TABLE utilisateurs           DISABLE ROW LEVEL SECURITY;

-- Vérification : doit retourner 0 ligne (aucune table avec RLS actif)
SELECT tablename, rowsecurity 
FROM pg_tables 
WHERE schemaname = 'public' AND rowsecurity = true;
