package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func write(t *testing.T, name, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestCheckExitCodes(t *testing.T) {
	t.Setenv("GITHUB_STEP_SUMMARY", "")
	p := write(t, "r.json", `{"models":[{"model":"m","overall_composite":0.7,"clean_pass_rate":0.7,"blast_radius":0,"duplicate_posts":0}]}`)
	var out, errb bytes.Buffer
	if code := run([]string{"check", "--results", p, "--min-composite", "0.5"}, nil, &out, &errb); code != 0 {
		t.Fatalf("want 0, got %d: %s %s", code, out.String(), errb.String())
	}
	out.Reset()
	if code := run([]string{"check", "--results", p, "--min-composite", "0.9"}, nil, &out, &errb); code != 1 {
		t.Fatalf("want 1, got %d", code)
	}
	if !strings.Contains(out.String(), "FAIL") {
		t.Fatal(out.String())
	}
}

func TestCheckWritesStepSummary(t *testing.T) {
	summary := filepath.Join(t.TempDir(), "summary.md")
	t.Setenv("GITHUB_STEP_SUMMARY", summary)
	p := write(t, "r.json", `{"models":[{"model":"m","overall_composite":1}]}`)
	var out, errb bytes.Buffer
	if code := run([]string{"check", "--results", p}, nil, &out, &errb); code != 0 {
		t.Fatalf("got %d", code)
	}
	if b, _ := os.ReadFile(summary); !strings.Contains(string(b), "toolproof gate") {
		t.Fatal("step summary not written")
	}
}

func TestLintFromStdinJSON(t *testing.T) {
	var out, errb bytes.Buffer
	in := strings.NewReader(`{"tools":[{"name":"t","description":"","inputSchema":{"type":"object"}}]}`)
	if code := run([]string{"lint", "--json"}, in, &out, &errb); code != 1 {
		t.Fatalf("want 1 for missing description, got %d", code)
	}
	if !strings.Contains(out.String(), `"rule": "E002"`) {
		t.Fatal(out.String())
	}
}

func TestUsageAndUnknown(t *testing.T) {
	var out, errb bytes.Buffer
	if run(nil, nil, &out, &errb) != 2 || run([]string{"bogus"}, nil, &out, &errb) != 2 {
		t.Fatal("expected usage errors")
	}
	if run([]string{"version"}, nil, &out, &errb) != 0 || !strings.Contains(out.String(), version) {
		t.Fatal("version")
	}
}
