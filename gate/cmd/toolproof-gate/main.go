// Command toolproof-gate is a dependency-free CI gate for toolproof eval results and a linter
// for MCP tool definitions.
//
//	toolproof-gate check --results reports/latest/results.json --min-composite 0.8 --max-blast-radius 0
//	toolproof-gate check --results new.json --baseline main.json --max-regression 0.03
//	toolproof-gate lint tools.json
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/SCVaitla/toolproof/gate/internal/lint"
	"github.com/SCVaitla/toolproof/gate/internal/results"
)

var version = "0.1.0"

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
}

func run(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		usage(stderr)
		return 2
	}
	switch args[0] {
	case "check":
		return runCheck(args[1:], stdout, stderr)
	case "lint":
		return runLint(args[1:], stdin, stdout, stderr)
	case "version", "--version", "-v":
		fmt.Fprintln(stdout, "toolproof-gate", version)
		return 0
	case "help", "--help", "-h":
		usage(stdout)
		return 0
	default:
		fmt.Fprintf(stderr, "unknown command %q\n", args[0])
		usage(stderr)
		return 2
	}
}

func usage(w io.Writer) {
	fmt.Fprint(w, `toolproof-gate: CI gates for MCP tool-use evals

Commands:
  check   Enforce thresholds on toolproof results.json (exit 1 on violation)
  lint    Lint MCP tool definitions (tools/list JSON) for LLM-usability defects
  version Print version

Run "toolproof-gate <command> -h" for flags.
`)
}

func runCheck(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("check", flag.ContinueOnError)
	fs.SetOutput(stderr)
	t := results.Disabled()
	path := fs.String("results", "reports/latest/results.json", "path to results.json")
	baselinePath := fs.String("baseline", "", "baseline results.json to compare against")
	fs.Float64Var(&t.MinComposite, "min-composite", -1, "minimum overall composite score per model")
	fs.Float64Var(&t.MinCleanPass, "min-pass-rate", -1, "minimum clean-condition pass rate per model")
	fs.Float64Var(&t.MinResilience, "min-resilience", -1, "minimum resilience index (stress/clean pass rate)")
	fs.Float64Var(&t.MaxBlastRadius, "max-blast-radius", -1, "maximum mean unrequested mutations per trial")
	fs.IntVar(&t.MaxDuplicates, "max-duplicates", -1, "maximum duplicate posts across all trials")
	fs.Float64Var(&t.MaxInvalidRate, "max-invalid-rate", -1, "maximum share of schema-invalid/hallucinated calls")
	fs.IntVar(&t.MaxInfraErrors, "max-infra-errors", -1, "maximum provider/harness errors per condition")
	fs.Float64Var(&t.MaxRegression, "max-regression", -1, "maximum composite drop vs --baseline")
	fs.StringVar(&t.ModelFilter, "model", "", "only gate models whose label contains this string")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	cur, err := results.Load(*path)
	if err != nil {
		fmt.Fprintln(stderr, "error:", err)
		return 2
	}
	var base *results.File
	if *baselinePath != "" {
		if base, err = results.Load(*baselinePath); err != nil {
			fmt.Fprintln(stderr, "error: baseline:", err)
			return 2
		}
	}
	violations := results.Check(cur, base, t)
	md := results.Markdown(cur, violations)
	fmt.Fprint(stdout, md)
	if summary := os.Getenv("GITHUB_STEP_SUMMARY"); summary != "" {
		if f, err := os.OpenFile(summary, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
			_, _ = f.WriteString(md + "\n")
			_ = f.Close()
		}
	}
	if len(violations) > 0 {
		return 1
	}
	return 0
}

func runLint(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("lint", flag.ContinueOnError)
	fs.SetOutput(stderr)
	strict := fs.Bool("strict", false, "treat warnings as errors")
	asJSON := fs.Bool("json", false, "emit findings as JSON")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	var in io.Reader = stdin
	name := "<stdin>"
	if fs.NArg() > 0 && fs.Arg(0) != "-" {
		f, err := os.Open(fs.Arg(0))
		if err != nil {
			fmt.Fprintln(stderr, "error:", err)
			return 2
		}
		defer f.Close()
		in, name = f, fs.Arg(0)
	}
	tools, err := lint.Parse(in)
	if err != nil {
		fmt.Fprintln(stderr, "error:", err)
		return 2
	}
	findings := lint.Run(tools)
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if findings == nil {
			findings = []lint.Finding{}
		}
		_ = enc.Encode(findings)
	} else {
		for _, f := range findings {
			fmt.Fprintln(stdout, f)
		}
		fmt.Fprintf(stdout, "%s: %d tool(s), %d finding(s)\n", name, len(tools), len(findings))
	}
	if lint.HasErrors(findings, *strict) {
		return 1
	}
	return 0
}
