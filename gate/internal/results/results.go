// Package results reads toolproof results.json and enforces CI quality gates on it.
package results

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
)

// ModelSummary mirrors one entry of results.json "models".
type ModelSummary struct {
	Model            string   `json:"model"`
	CleanPassRate    *float64 `json:"clean_pass_rate"`
	StressPassRate   *float64 `json:"stress_pass_rate"`
	ResilienceIndex  *float64 `json:"resilience_index"`
	OverallComposite *float64 `json:"overall_composite"`
	BlastRadius      *float64 `json:"blast_radius"`
	DuplicatePosts   int      `json:"duplicate_posts"`
}

// ConditionSummary mirrors one entry of results.json "summary".
type ConditionSummary struct {
	Model           string   `json:"model"`
	Condition       string   `json:"condition"`
	ValidTrials     int      `json:"valid_trials"`
	InfraErrors     int      `json:"infra_errors"`
	PassRate        *float64 `json:"pass_rate"`
	InvalidCallRate *float64 `json:"invalid_call_rate"`
}

// File is the subset of results.json the gate needs.
type File struct {
	Models  []ModelSummary     `json:"models"`
	Summary []ConditionSummary `json:"summary"`
}

// Load parses a results.json file.
func Load(path string) (*File, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return Parse(f)
}

// Parse decodes results.json from a reader.
func Parse(r io.Reader) (*File, error) {
	var out File
	if err := json.NewDecoder(r).Decode(&out); err != nil {
		return nil, fmt.Errorf("decode results: %w", err)
	}
	if len(out.Models) == 0 {
		return nil, fmt.Errorf("results contain no models")
	}
	return &out, nil
}

// Thresholds configure the gate. Negative values disable a check.
type Thresholds struct {
	MinComposite   float64
	MinCleanPass   float64
	MinResilience  float64
	MaxBlastRadius float64
	MaxDuplicates  int
	MaxInvalidRate float64
	MaxInfraErrors int
	MaxRegression  float64 // allowed composite drop vs. baseline
	ModelFilter    string
}

// Disabled returns thresholds with every check turned off.
func Disabled() Thresholds {
	return Thresholds{-1, -1, -1, -1, -1, -1, -1, -1, ""}
}

// Violation is one failed check.
type Violation struct {
	Model   string
	Check   string
	Actual  string
	Allowed string
}

func (v Violation) String() string {
	return fmt.Sprintf("%s: %s = %s (allowed %s)", v.Model, v.Check, v.Actual, v.Allowed)
}

func val(p *float64) (float64, bool) {
	if p == nil {
		return 0, false
	}
	return *p, true
}

// Check evaluates thresholds (and an optional baseline) and returns every violation.
func Check(cur *File, baseline *File, t Thresholds) []Violation {
	var out []Violation
	add := func(m, check, actual, allowed string) {
		out = append(out, Violation{m, check, actual, allowed})
	}
	base := map[string]ModelSummary{}
	if baseline != nil {
		for _, m := range baseline.Models {
			base[m.Model] = m
		}
	}
	for _, m := range cur.Models {
		if t.ModelFilter != "" && !strings.Contains(m.Model, t.ModelFilter) {
			continue
		}
		if v, ok := val(m.OverallComposite); t.MinComposite >= 0 && (!ok || v < t.MinComposite) {
			add(m.Model, "composite", fmtPtr(m.OverallComposite), fmt.Sprintf(">= %.3f", t.MinComposite))
		}
		if v, ok := val(m.CleanPassRate); t.MinCleanPass >= 0 && (!ok || v < t.MinCleanPass) {
			add(m.Model, "clean_pass_rate", fmtPtr(m.CleanPassRate), fmt.Sprintf(">= %.3f", t.MinCleanPass))
		}
		if v, ok := val(m.ResilienceIndex); t.MinResilience >= 0 && ok && v < t.MinResilience {
			add(m.Model, "resilience_index", fmtPtr(m.ResilienceIndex), fmt.Sprintf(">= %.3f", t.MinResilience))
		}
		if v, ok := val(m.BlastRadius); t.MaxBlastRadius >= 0 && ok && v > t.MaxBlastRadius {
			add(m.Model, "blast_radius", fmtPtr(m.BlastRadius), fmt.Sprintf("<= %.3f", t.MaxBlastRadius))
		}
		if t.MaxDuplicates >= 0 && m.DuplicatePosts > t.MaxDuplicates {
			add(m.Model, "duplicate_posts", fmt.Sprint(m.DuplicatePosts), fmt.Sprintf("<= %d", t.MaxDuplicates))
		}
		if b, ok := base[m.Model]; ok && t.MaxRegression >= 0 {
			cv, cok := val(m.OverallComposite)
			bv, bok := val(b.OverallComposite)
			if cok && bok && bv-cv > t.MaxRegression {
				add(m.Model, "composite_regression", fmt.Sprintf("%.3f -> %.3f", bv, cv), fmt.Sprintf("drop <= %.3f", t.MaxRegression))
			}
		}
	}
	for _, s := range cur.Summary {
		if t.ModelFilter != "" && !strings.Contains(s.Model, t.ModelFilter) {
			continue
		}
		label := s.Model + " [" + s.Condition + "]"
		if v, ok := val(s.InvalidCallRate); t.MaxInvalidRate >= 0 && ok && v > t.MaxInvalidRate {
			add(label, "invalid_call_rate", fmtPtr(s.InvalidCallRate), fmt.Sprintf("<= %.3f", t.MaxInvalidRate))
		}
		if t.MaxInfraErrors >= 0 && s.InfraErrors > t.MaxInfraErrors {
			add(label, "infra_errors", fmt.Sprint(s.InfraErrors), fmt.Sprintf("<= %d", t.MaxInfraErrors))
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Model < out[j].Model })
	return out
}

func fmtPtr(p *float64) string {
	if p == nil {
		return "n/a"
	}
	return fmt.Sprintf("%.3f", *p)
}

// Markdown renders a compact leaderboard suitable for $GITHUB_STEP_SUMMARY.
func Markdown(f *File, violations []Violation) string {
	var b strings.Builder
	b.WriteString("### toolproof gate\n\n| Model | Composite | Clean pass | Resilience | Blast radius | Duplicates |\n|---|---:|---:|---:|---:|---:|\n")
	for _, m := range f.Models {
		fmt.Fprintf(&b, "| `%s` | %s | %s | %s | %s | %d |\n", m.Model, fmtPtr(m.OverallComposite),
			fmtPtr(m.CleanPassRate), fmtPtr(m.ResilienceIndex), fmtPtr(m.BlastRadius), m.DuplicatePosts)
	}
	if len(violations) == 0 {
		b.WriteString("\n**PASS**: all gates satisfied.\n")
		return b.String()
	}
	fmt.Fprintf(&b, "\n**FAIL**: %d gate violation(s):\n\n", len(violations))
	for _, v := range violations {
		fmt.Fprintf(&b, "- %s\n", v)
	}
	return b.String()
}
