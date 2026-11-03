// Package lint checks MCP tool definitions for common schema and usability problems.
package lint

import (
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"sort"
	"strings"
)

// Severity of a finding.
type Severity string

const (
	Error   Severity = "error"
	Warning Severity = "warning"
)

// Finding is one lint result.
type Finding struct {
	Tool     string   `json:"tool"`
	Rule     string   `json:"rule"`
	Severity Severity `json:"severity"`
	Message  string   `json:"message"`
}

func (f Finding) String() string {
	return fmt.Sprintf("%-7s %s %s: %s", f.Severity, f.Rule, f.Tool, f.Message)
}

// Tool is the subset of an MCP Tool definition we lint.
type Tool struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	InputSchema map[string]any `json:"inputSchema"`
	Annotations map[string]any `json:"annotations"`
}

// Parse accepts either {"tools": [...]} (tools/list result) or a bare array.
func Parse(r io.Reader) ([]Tool, error) {
	raw, err := io.ReadAll(r)
	if err != nil {
		return nil, err
	}
	var wrapped struct {
		Tools []Tool `json:"tools"`
	}
	if err := json.Unmarshal(raw, &wrapped); err == nil && wrapped.Tools != nil {
		return wrapped.Tools, nil
	}
	var bare []Tool
	if err := json.Unmarshal(raw, &bare); err != nil {
		return nil, fmt.Errorf("expected {\"tools\": [...]} or a JSON array of tools: %w", err)
	}
	return bare, nil
}

var (
	namePattern  = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,64}$`)
	genericNames = map[string]bool{"data": true, "input": true, "value": true, "params": true,
		"args": true, "payload": true, "obj": true, "body": true, "info": true}
)

const (
	minDescription = 30
	maxDepth       = 3
	maxParams      = 12
)

// Run lints all tools.
func Run(tools []Tool) []Finding {
	var findings []Finding
	seen := map[string]int{}
	for _, tool := range tools {
		seen[tool.Name]++
		findings = append(findings, lintTool(tool)...)
	}
	for name, count := range seen {
		if count > 1 {
			findings = append(findings, Finding{name, "E005", Error, fmt.Sprintf("tool name defined %d times", count)})
		}
	}
	sort.SliceStable(findings, func(i, j int) bool {
		if findings[i].Severity != findings[j].Severity {
			return findings[i].Severity == Error
		}
		if findings[i].Tool != findings[j].Tool {
			return findings[i].Tool < findings[j].Tool
		}
		return findings[i].Rule < findings[j].Rule
	})
	return findings
}

func lintTool(tool Tool) []Finding {
	var findings []Finding
	add := func(rule string, severity Severity, message string, args ...any) {
		findings = append(findings, Finding{tool.Name, rule, severity, fmt.Sprintf(message, args...)})
	}
	if !namePattern.MatchString(tool.Name) {
		add("E001", Error, "name must match %s", namePattern)
	}
	desc := strings.TrimSpace(tool.Description)
	switch {
	case desc == "":
		add("E002", Error, "missing description; models choose tools by reading it")
	case len(desc) < minDescription:
		add("W001", Warning, "description is only %d chars; say what it does and when to use it", len(desc))
	}

	schema := tool.InputSchema
	if schema == nil || schema["type"] != "object" {
		add("E003", Error, "inputSchema must be a JSON Schema with type \"object\"")
		return findings
	}
	props, _ := schema["properties"].(map[string]any)
	if len(props) > maxParams {
		add("W007", Warning, "%d parameters; consider splitting the tool (models degrade past ~%d)", len(props), maxParams)
	}
	if req, ok := schema["required"].([]any); ok {
		for _, r := range req {
			name, _ := r.(string)
			if _, exists := props[name]; !exists {
				add("E004", Error, "required parameter %q is not defined in properties", name)
			}
		}
	}
	names := make([]string, 0, len(props))
	for name := range props {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		p, _ := props[name].(map[string]any)
		if d, _ := p["description"].(string); strings.TrimSpace(d) == "" {
			add("W002", Warning, "parameter %q has no description", name)
		}
		if genericNames[strings.ToLower(name)] {
			add("W003", Warning, "parameter name %q is too generic to guide a model", name)
		}
		if d := depth(p, 0); d > maxDepth {
			add("W004", Warning, "parameter %q nests %d levels deep; flatten it", name, d)
		}
	}

	if tool.Annotations == nil {
		add("W005", Warning, "no annotations; set readOnlyHint so clients can auto-approve safe tools")
		return findings
	}
	if readOnly, _ := tool.Annotations["readOnlyHint"].(bool); !readOnly {
		_, hasIdempotent := tool.Annotations["idempotentHint"]
		_, hasDestructive := tool.Annotations["destructiveHint"]
		if !hasIdempotent || !hasDestructive {
			add("W006", Warning, "write tool should declare idempotentHint and destructiveHint (retry safety)")
		}
	}
	return findings
}

func depth(node any, d int) int {
	m, ok := node.(map[string]any)
	if !ok {
		return d
	}
	best := d
	if props, ok := m["properties"].(map[string]any); ok {
		for _, child := range props {
			if c := depth(child, d+1); c > best {
				best = c
			}
		}
	}
	if items, ok := m["items"]; ok {
		if c := depth(items, d+1); c > best {
			best = c
		}
	}
	return best
}

// HasErrors reports whether any finding is an error (or a warning when strict).
func HasErrors(findings []Finding, strict bool) bool {
	for _, f := range findings {
		if f.Severity == Error || strict {
			return true
		}
	}
	return false
}
