CREATE TABLE groups (
 id TEXT NOT NULL PRIMARY KEY, name TEXT NOT NULL,
 name_key TEXT NOT NULL UNIQUE, archived_at TEXT
);
CREATE TABLE subjects (
 id TEXT NOT NULL PRIMARY KEY, name TEXT NOT NULL,
 name_key TEXT NOT NULL UNIQUE,
 is_system INTEGER NOT NULL DEFAULT 0 CHECK(is_system IN (0,1)),
 archived_at TEXT,
 CHECK(is_system=0 OR archived_at IS NULL)
);
CREATE UNIQUE INDEX one_system_subject ON subjects(is_system) WHERE is_system=1;
CREATE TABLE topics (
 id TEXT NOT NULL PRIMARY KEY,
 subject_id TEXT NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
 name TEXT NOT NULL, name_key TEXT NOT NULL, archived_at TEXT,
 UNIQUE(subject_id,name_key)
);
CREATE TABLE group_access (
 group_id TEXT NOT NULL PRIMARY KEY REFERENCES groups(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
 password_hash TEXT, is_open INTEGER NOT NULL DEFAULT 0 CHECK(is_open IN (0,1)),
 closes_at_utc TEXT, version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1)
);
CREATE TABLE students (
 id TEXT NOT NULL PRIMARY KEY,
 group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
 display_name TEXT NOT NULL CHECK(length(trim(display_name))>0), archived_at TEXT,
 UNIQUE(id,group_id)
);
CREATE TABLE class_assignments (
 id TEXT NOT NULL PRIMARY KEY,
 group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
 topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
 lesson_date TEXT NOT NULL CHECK(lesson_date GLOB '????-??-??'),
 pair_number INTEGER NOT NULL CHECK(pair_number BETWEEN 1 AND 6),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT,
 UNIQUE(id,group_id,topic_id)
);
CREATE UNIQUE INDEX assignment_active_slot
 ON class_assignments(group_id,lesson_date,pair_number) WHERE archived_at IS NULL;
CREATE INDEX assignments_group_topic_date ON class_assignments(group_id,topic_id,lesson_date);
CREATE TABLE submissions (
 id TEXT NOT NULL PRIMARY KEY, student_id TEXT NOT NULL,
 group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
 topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
 assignment_id TEXT, attempt_number INTEGER NOT NULL CHECK(attempt_number>=1),
 review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','reviewed')),
 reviewed_at TEXT, created_at TEXT NOT NULL,
 CHECK(review_status='reviewed' OR reviewed_at IS NULL),
 UNIQUE(student_id,topic_id,attempt_number),
 FOREIGN KEY(student_id,group_id) REFERENCES students(id,group_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
 FOREIGN KEY(assignment_id,group_id,topic_id) REFERENCES class_assignments(id,group_id,topic_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE INDEX submissions_group_topic ON submissions(group_id,topic_id,created_at);
CREATE TABLE submission_files (
 id TEXT NOT NULL PRIMARY KEY,
 submission_id TEXT NOT NULL UNIQUE REFERENCES submissions(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
 original_filename TEXT NOT NULL, stored_path TEXT NOT NULL UNIQUE,
 size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
 sha256 TEXT CHECK(sha256 IS NULL OR length(sha256)=64)
);
PRAGMA user_version=2;
