-- Table des offres. La région n'est jamais stockée : elle est dérivée de department + country.
-- AUTOINCREMENT garantit qu'un id (numéro de dossier) n'est jamais réutilisé après suppression.
CREATE TABLE offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    contract_type TEXT,
    company TEXT,
    location TEXT,
    department TEXT CHECK (department IS NULL OR typeof(department) = 'text'),
    country TEXT NOT NULL DEFAULT 'FR'
        CHECK (length(country) = 2 AND country = upper(country)),
    status TEXT CHECK (status IS NULL OR status IN
        ('À postuler', 'En cours', 'Entretien', 'Offre reçue', 'Rejeté', 'Abandon')),
    main_issue TEXT,
    rejection_reason TEXT,
    source TEXT,
    applied_at TEXT,
    application_sent INTEGER NOT NULL DEFAULT 0 CHECK (application_sent IN (0, 1)),
    follow_up_done INTEGER NOT NULL DEFAULT 0 CHECK (follow_up_done IN (0, 1)),
    deadline TEXT,
    deadline_note TEXT,
    description TEXT,
    links TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_offers_status ON offers (status);
CREATE INDEX idx_offers_department ON offers (department);
CREATE INDEX idx_offers_deadline ON offers (deadline);
