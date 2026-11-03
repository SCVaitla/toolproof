package lint

import (
	"strings"
	"testing"
)

func rules(fs []Finding) map[string]bool {
	m := map[string]bool{}
	for _, f := range fs {
		m[f.Rule] = true
	}
	return m
}

func TestCleanToolHasNoFindings(t *testing.T) {
	tools, err := Parse(strings.NewReader(`{"tools":[{
		"name":"slack_get_messages",
		"description":"Get top-level messages from a channel, newest first.",
		"inputSchema":{"type":"object","properties":{"channel":{"type":"string","description":"Channel name or ID"}},"required":["channel"]},
		"annotations":{"readOnlyHint":true}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	if f := Run(tools); len(f) != 0 {
		t.Fatalf("expected no findings, got %v", f)
	}
}

func TestDetectsCommonDefects(t *testing.T) {
	tools, err := Parse(strings.NewReader(`[
		{"name":"bad name!","description":"","inputSchema":{"type":"object",
		  "properties":{"data":{"type":"object","properties":{"a":{"type":"object","properties":{"b":{"type":"object",
		    "properties":{"c":{"type":"object","properties":{"d":{"type":"string"}}}}}}}}}},
		  "required":["missing"]}},
		{"name":"send","description":"Send.","inputSchema":{"type":"object","properties":{}},
		  "annotations":{"readOnlyHint":false}},
		{"name":"send","description":"Duplicate name for testing purposes here.","inputSchema":{"type":"string"}}
	]`))
	if err != nil {
		t.Fatal(err)
	}
	got := rules(Run(tools))
	for _, want := range []string{"E001", "E002", "E003", "E004", "E005", "W001", "W002", "W003", "W004", "W005", "W006"} {
		if !got[want] {
			t.Errorf("expected rule %s to fire; got %v", want, got)
		}
	}
}

func TestErrorsSortFirstAndStrictMode(t *testing.T) {
	tools, _ := Parse(strings.NewReader(`[{"name":"x","description":"short","inputSchema":{"type":"object","properties":{}},
		"annotations":{"readOnlyHint":true}}]`))
	fs := Run(tools)
	if HasErrors(fs, false) {
		t.Fatal("only warnings expected")
	}
	if !HasErrors(fs, true) {
		t.Fatal("strict mode must fail on warnings")
	}
}

func TestParseRejectsGarbage(t *testing.T) {
	if _, err := Parse(strings.NewReader(`{"nope": 1`)); err == nil {
		t.Fatal("expected parse error")
	}
}
