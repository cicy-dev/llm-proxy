-- LLM QA History Database Schema
-- Run this SQL to create the database and table

CREATE DATABASE IF NOT EXISTS llm_qa_history 
CHARACTER SET utf8mb4 
COLLATE utf8mb4_unicode_ci;

USE llm_qa_history;

CREATE TABLE IF NOT EXISTS llm_qa_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    url TEXT NOT NULL,
    method VARCHAR(16) NOT NULL DEFAULT '',
    status_code INT DEFAULT 0,
    request_body LONGTEXT,
    response_body LONGTEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_created_at (created_at),
    INDEX idx_status_code (status_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
