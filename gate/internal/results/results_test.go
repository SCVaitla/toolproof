package results

import (
	"strings"
	"testing"
)

const sample = `{
  "models": [
    {"model": "mock:golden", "clean_pass_rate": 1.0, "stress_pass_rate": 1.0, "resilience_index": 1.0,
     "overall_composite": 1.0, "blast_radius": 0.0, "duplicate_posts": 0},
    {"model": "mock:flawed", "clean_pass_rate": 0.1, "stress_pass_rate": 0.05, "resilience_index": 0.5,
     "overall_composite": 0.52, "blast_radius": 0.6, "duplicate_posts": 3}
  ],
  "summary": [
    {"model": "mock:golden", "condition": "clean", "valid_trials": 10, "infra_errors": 0, "pass_rate": 1.0, "invalid_call_rate": 0.0},
    {"model": "mock:flawed", "condition": "chaos", "valid_trials": 10, "infra_errors": 2, "pass_rate": 0.05, "invalid_call_rate": 0.2}
  ]
}`

func mustParse(t *testing.T, s string) *File {
	t.Helper()
	f, err := Parse(strings.NewReader(s))
	if err != nil {
		t.Fatal(err)
	}
	return f
}

func TestDisabledThresholdsPass(t *testing.T) {
	if v := Check(mustParse(t, sample), nil, Disabled()); len(v) != 0 {
		t.Fatalf("expected no violations, got %v", v)
	}
}

func TestThresholdsFlagFlawedModel(t *testing.T) {
	th := Disabled()
	th.MinComposite = 0.8
	th.MaxBlastRadius = 0
	th.MaxDuplicates = 0
	th.MinResilience = 0.9
	th.MaxInvalidRate = 0.1
	th.MaxInfraErrors = 0
	v := Check(mustParse(t, sample), nil, th)
	checks := map[string]bool{}
	for _, x := range v {
		if !strings.HasPrefix(x.Model, "mock:flawed") {
			t.Fatalf("golden model should pass, got %v", x)
		}
		checks[x.Check] = true
	}
	for _, want := range []string{"composite", "blast_radius", "duplicate_posts", "resilience_index", "invalid_call_rate", "infra_errors"} {
		if !checks[want] {
			t.Errorf("missing violation %q in %v", want, v)
		}
	}
}

func TestModelFilter(t *testing.T) {
	th := Disabled()
	th.MinComposite = 0.8
	th.ModelFilter = "golden"
	if v := Check(mustParse(t, sample), nil, th); len(v) != 0 {
		t.Fatalf("filter should skip flawed model: %v", v)
	}
}

func TestBaselineRegression(t *testing.T) {
	base := mustParse(t, sample)
	cur := mustParse(t, strings.Replace(sample, `"overall_composite": 1.0`, `"overall_composite": 0.9`, 1))
	th := Disabled()
	th.MaxRegression = 0.05
	v := Check(cur, base, th)
	if len(v) != 1 || v[0].Check != "composite_regression" {
		t.Fatalf("expected one regression violation, got %v", v)
	}
}

func TestNullMetricsFailMinimums(t *testing.T) {
	f := mustParse(t, `{"models":[{"model":"x","overall_composite":null,"clean_pass_rate":null}]}`)
	th := Disabled()
	th.MinComposite = 0.5
	th.MinCleanPass = 0.5
	if v := Check(f, nil, th); len(v) != 2 {
		t.Fatalf("missing metrics must fail minimum gates, got %v", v)
	}
}

func TestParseRejectsEmpty(t *testing.T) {
	if _, err := Parse(strings.NewReader(`{"models": []}`)); err == nil {
		t.Fatal("expected error for empty models")
	}
}

func TestMarkdown(t *testing.T) {
	f := mustParse(t, sample)
	if md := Markdown(f, nil); !strings.Contains(md, "PASS") || !strings.Contains(md, "`mock:golden`") {
		t.Fatal(md)
	}
	if md := Markdown(f, []Violation{{"m", "c", "1", "0"}}); !strings.Contains(md, "FAIL") {
		t.Fatal(md)
	}
}
