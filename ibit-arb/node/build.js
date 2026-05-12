#!/usr/bin/env node
'use strict';

const fs   = require('fs');
const path = require('path');

const rootDir = __dirname;
const distDir = path.join(rootDir, 'dist');

const requiredFiles = [
  ['public/index.html',        'index.html'],
  ['staticwebapp.config.json', 'staticwebapp.config.json'],
];

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyFile(from, to) {
  const src = path.join(rootDir, from);
  const dst = path.join(distDir, to);
  if (!fs.existsSync(src)) {
    throw new Error(`Missing required build input: ${from}`);
  }
  ensureDir(path.dirname(dst));
  fs.copyFileSync(src, dst);
  console.log(`  copied ${from} → dist/${to}`);
}

function cleanDist() {
  fs.rmSync(distDir, { recursive: true, force: true });
  ensureDir(distDir);
}

function verifyDist() {
  const required = ['index.html', 'staticwebapp.config.json'];
  const missing = required.filter((f) => !fs.existsSync(path.join(distDir, f)));
  if (missing.length > 0) {
    throw new Error(`Build output is missing required files: ${missing.join(', ')}`);
  }
}

function main() {
  console.log('Building static dist…');
  cleanDist();
  for (const [from, to] of requiredFiles) {
    copyFile(from, to);
  }
  verifyDist();
  console.log(`Built dist at ${distDir}`);
}

main();
