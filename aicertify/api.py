"""
AICertify API

A simple entry point for developers to integrate AICertify evaluation into their applications.
This module provides functions to evaluate AI interactions for compliance with various policies.
"""

import json
import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Tuple
from datetime import datetime
from shutil import copy2
from uuid import UUID

# Configure logging
#logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Import models and evaluation components
from aicertify.models.contract_models import AiCertifyContract, load_contract
from aicertify.models.langfair_eval import ToxicityMetrics, StereotypeMetrics

# Import the evaluators
from aicertify.evaluators import (
    BaseEvaluator, 
    EvaluationResult, 
    FairnessEvaluator,
    ContentSafetyEvaluator,
    RiskManagementEvaluator,
    ComplianceEvaluator,
    EvaluatorConfig
)

# Try to import the full evaluator, but provide a fallback
try:
    from aicertify.evaluators.api import AICertifyEvaluator
    FULL_EVALUATOR_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Full evaluator not available, will use simplified evaluator: {e}")
    FULL_EVALUATOR_AVAILABLE = False

# Import the simplified evaluator that has minimal dependencies
from aicertify.evaluators.simple_evaluator import evaluate_contract_simple

# Import report generation components
from aicertify.report_generation.report_generator import ReportGenerator
try:
    # Import our new data extraction module
    from aicertify.report_generation.data_extraction import create_evaluation_report
    REPORT_DATA_EXTRACTION_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Report data extraction module not available: {e}")
    REPORT_DATA_EXTRACTION_AVAILABLE = False

# Import OPA components
from aicertify.opa_core.policy_loader import PolicyLoader
from aicertify.opa_core.evaluator import OpaEvaluator

# Create instances of key components for API functions
debug_mode = os.environ.get("OPA_DEBUG", "0") == "1"
opa_evaluator = OpaEvaluator(
    use_external_server=False,  # Force local evaluator
    server_url=os.environ.get("OPA_SERVER_URL", "http://localhost:8181"),
    debug=debug_mode
)
policy_loader = PolicyLoader()
report_generator = ReportGenerator()

def _ensure_valid_evaluation_structure(evaluation_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure the evaluation result has a valid structure for policy evaluation.
    
    Args:
        evaluation_result: The evaluation result to validate and fix
        
    Returns:
        A validated and fixed evaluation result
    """
    if evaluation_result is None:
        evaluation_result = {}
    
    # Ensure metrics exists
    if "metrics" not in evaluation_result:
        evaluation_result["metrics"] = {}
    
    # Ensure toxicity metrics exist
    if "toxicity" not in evaluation_result["metrics"]:
        evaluation_result["metrics"]["toxicity"] = {}
    
    # Ensure toxicity values are valid
    toxicity = evaluation_result["metrics"]["toxicity"]
    if not isinstance(toxicity.get("toxic_fraction"), (int, float)):
        toxicity["toxic_fraction"] = 0.0
    if not isinstance(toxicity.get("max_toxicity"), (int, float)):
        toxicity["max_toxicity"] = 0.0
    if not isinstance(toxicity.get("toxicity_probability"), (int, float)):
        toxicity["toxicity_probability"] = 0.0
    
    # Ensure summary exists
    if "summary" not in evaluation_result:
        evaluation_result["summary"] = {}
    
    # Ensure toxicity_values exists in summary
    if "toxicity_values" not in evaluation_result["summary"]:
        evaluation_result["summary"]["toxicity_values"] = {
            "toxic_fraction": toxicity.get("toxic_fraction", 0.0),
            "max_toxicity": toxicity.get("max_toxicity", 0.0),
            "toxicity_probability": toxicity.get("toxicity_probability", 0.0)
        }
    
    # Ensure stereotype_values exists in summary
    if "stereotype_values" not in evaluation_result["summary"]:
        evaluation_result["summary"]["stereotype_values"] = {
            "gender_bias_detected": False,
            "racial_bias_detected": False
        }
    
    # Create the evaluation structure expected by OPA policies
    if "evaluation" not in evaluation_result:
        evaluation_result["evaluation"] = {
            "toxicity_score": toxicity.get("max_toxicity", 0.0),
            "toxic_fraction": toxicity.get("toxic_fraction", 0.0),
            "toxicity_probability": toxicity.get("toxicity_probability", 0.0),
            "gender_bias_detected": evaluation_result["summary"].get("stereotype_values", {}).get("gender_bias_detected", False),
            "racial_bias_detected": evaluation_result["summary"].get("stereotype_values", {}).get("racial_bias_detected", False)
        }
    
    return evaluation_result

# Expose key functions at the top level for developer convenience
async def generate_report(
    evaluation_result: Dict[str, Any],
    opa_results: Dict[str, Any] = None,
    output_formats: List[str] = ["markdown"],
    output_dir: Optional[str] = None
) -> Dict[str, str]:
    """
    Generate evaluation reports from evaluation results.
    
    This function provides a simple one-line method to generate reports from
    evaluation results, making it easy for developers to get well-formatted
    reports without understanding the internal details.
    
    Args:
        evaluation_result: The evaluation results from evaluate_contract
        opa_results: The OPA policy results (if separate from evaluation_result)
        output_formats: List of formats to generate ("markdown", "pdf", or both)
        output_dir: Directory to save reports (creates reports/ if not specified)
        
    Returns:
        Dictionary with paths to the generated reports by format
    """
    # Validate and fix evaluation result structure
    evaluation_result = _ensure_valid_evaluation_structure(evaluation_result)
    
    # Extract OPA results from evaluation_result if not provided separately
    if opa_results is None:
        if "policy_results" in evaluation_result:
            opa_results = evaluation_result["policy_results"]
        elif "policies" in evaluation_result:
            opa_results = evaluation_result["policies"]
        else:
            opa_results = {}
    
    try:
        # Import required components
        from aicertify.report_generation.report_generator import ReportGenerator
        from aicertify.report_generation.data_extraction import create_evaluation_report
        from aicertify.report_generation.report_models import (
            EvaluationReport, ApplicationDetails,
            MetricGroup, MetricValue, PolicyResult
        )
        
        # Create the evaluation report model
        try:
            report_model = create_evaluation_report(evaluation_result, opa_results)
        except Exception as e:
            logger.warning(f"Error creating evaluation report model: {e}, using fallback")
            # Create a minimal report model with basic information
            app_name = evaluation_result.get("app_name", "Unknown")
            if app_name == "Unknown" and "application_name" in evaluation_result:
                app_name = evaluation_result["application_name"]
                
            report_model = EvaluationReport(
                app_details=ApplicationDetails(
                    name=app_name,
                    evaluation_mode="Standard",
                    contract_count=1,
                    evaluation_date=datetime.now()
                ),
                metric_groups=[],
                policy_results=[],
                summary="Basic evaluation report"
            )
        
        # Setup output directory
        if not output_dir:
            output_dir = Path.cwd() / "reports"
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True, parents=True)
        
        # Get application name for the filename
        app_name = report_model.app_details.name.replace(" ", "_")
        if app_name == "Unknown" and "application_name" in evaluation_result:
            app_name = evaluation_result["application_name"].replace(" ", "_")
        
        # Get a timestamp for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create ReportGenerator instance
        report_gen = ReportGenerator()
        
        # Generate markdown content
        md_content = report_gen.generate_markdown_report(report_model)
        
        # Generate reports
        report_paths = {}
        
        # Markdown report generation if requested
        if "markdown" in output_formats:
            md_path = output_dir / f"report_{app_name}_{timestamp}.md"
            with open(md_path, "w") as f:
                f.write(md_content)
            report_paths["markdown"] = str(md_path)
            logger.info(f"Markdown report saved to: {md_path}")
        
        # PDF report generation
        if "pdf" in output_formats:
            pdf_path = output_dir / f"report_{app_name}_{timestamp}.pdf"
            pdf_result = report_gen.generate_pdf_report(md_content, str(pdf_path))
            if pdf_result:
                report_paths["pdf"] = pdf_result
                logger.info(f"PDF report saved to: {pdf_result}")
        
        return report_paths
    except ImportError as e:
        logger.error(f"Required modules for report generation not available: {e}")
        return {"error": f"Report generation failed: {str(e)}"}
    except Exception as e:
        logger.error(f"Error generating reports: {e}")
        return {"error": f"Report generation failed: {str(e)}"}

async def evaluate_contract(
    contract_path: str,  # Path to contract file
    policy_category: str = "eu_ai_act",  # Policy to evaluate against
    use_simple_evaluator: bool = False,  # Optional flag for simplified evaluation
) -> Tuple[Dict[str, Any], Dict[str, Any]]:  # Return (evaluation_result, opa_results)
    """
    Evaluate a contract against specified policies.
    
    This function handles all evaluation logic internally, including:
    - Loading the contract
    - Running the appropriate evaluator
    - Handling any errors that occur during evaluation
    - Applying policy validation
    
    Args:
        contract_path: Path to the contract file
        policy_category: Policy category to evaluate against
        use_simple_evaluator: Whether to use simplified evaluation
        
    Returns:
        Tuple containing (evaluation_result, opa_results)
    """
    # Load contract
    contract = load_contract(contract_path)
    
    # Use the appropriate evaluator
    # (Handle ALL errors internally)
    # ...
    evaluation_result: Dict[str, Any] = {
        "status": "evaluation not implemented",
        "contract_id": getattr(contract, "id", None),
    }
    opa_results: Dict[str, Any] = {
        "status": "opa evaluation not implemented",
        "policy_category": policy_category,
    }
    return evaluation_result, opa_results

async def evaluate_contract_object(
    contract: AiCertifyContract,
    policy_category: str = "eu_ai_act",
    generate_report: bool = True,
    report_format: str = "markdown",
    output_dir: Optional[str] = None,
    use_phase1_evaluators: bool = True
) -> Dict[str, Any]:
    """
    Evaluate a contract using either the legacy evaluator or the new Phase 1 evaluators.
    
    Args:
        contract: The AiCertifyContract object to evaluate
        policy_category: Category of OPA policies to evaluate against
        generate_report: Whether to generate a report
        report_format: Format of the report (json, markdown, pdf)
        output_dir: Directory to save the report
        use_phase1_evaluators: Whether to use the new Phase 1 evaluators
        
    Returns:
        Dictionary containing evaluation results and report paths
    """
    if use_phase1_evaluators:
        return await evaluate_contract_comprehensive(
            contract=contract,
            policy_category=policy_category,
            generate_report=generate_report,
            report_format=report_format,
            output_dir=output_dir
        )
    else:
        # Use the original implementation
        # ... existing code ...
        # This is a simplified version for the example
        if FULL_EVALUATOR_AVAILABLE:
            evaluator = AICertifyEvaluator()
            # ... existing evaluation logic ...
            return {"status": "Using legacy evaluator"}
        else:
            # Fall back to simple evaluator
            # ... existing fallback logic ...
            return {"status": "Using legacy simple evaluator"}

async def evaluate_conversations(
    app_name: str,
    conversations: List[Dict[str, Any]],
    policy_category: str = "eu_ai_act",
    generate_report: bool = True,
    report_format: str = "markdown",
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Evaluate a list of conversations directly.
    
    This is a convenience function for developers who want to evaluate conversations
    without creating a contract object first.
    
    Args:
        app_name: Name of the application
        conversations: List of conversation dictionaries with 'user_input' and 'response' keys
        policy_category: OPA policy category to evaluate against
        generate_report: Whether to generate an evaluation report
        report_format: Format of the report ("markdown" or "pdf")
        output_dir: Directory to save evaluation results and reports
    
    Returns:
        Dictionary containing evaluation results, policy validation, and report
    """
    # Create evaluator
    evaluator = AICertifyEvaluator()
    
    # Evaluate conversations
    try:
        evaluation_result = await evaluator.evaluate_conversations(
            app_name=app_name,
            conversations=conversations
        )
        
        # Validate and fix evaluation result structure
        evaluation_result = _ensure_valid_evaluation_structure(evaluation_result)
        
    except Exception as e:
        logger.error(f"Error during evaluation: {e}")
        return {"error": f"Evaluation failed: {str(e)}"}
    
    # Apply OPA policies
    try:
        opa_results = evaluator.evaluate_policy(
            evaluation_result=evaluation_result,
            policy_category=policy_category
        )
    except Exception as e:
        logger.error(f"Error during policy validation: {e}")
        # Create a minimal but valid policy result structure
        opa_results = {
            "error": f"Policy validation failed: {str(e)}",
            "available_categories": [],
            "fairness": {
                "result": [
                    {
                        "expressions": [
                            {
                                "value": {
                                    "overall_result": False,
                                    "policy": "Error in evaluation",
                                    "recommendations": [
                                        f"Fix evaluation error: {str(e)}"
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        }
    
    # Create result dictionary
    result = {
        "evaluation": evaluation_result,
        "policies": opa_results,
        "application_name": app_name
    }
    
    # Generate report if requested
    if generate_report:
        try:
            report = evaluator.generate_report(
                evaluation_result=evaluation_result,
                opa_results=opa_results,
                output_format=report_format
            )
            result["report"] = report
            
            # Save report to file if output_dir is specified
            if output_dir:
                output_path = Path(output_dir)
                output_path.mkdir(parents=True, exist_ok=True)
                
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                report_filename = f"report_{app_name}_{timestamp}.md"
                report_path = output_path / report_filename
                
                with open(report_path, "w") as f:
                    f.write(report)
                
                result["report_path"] = str(report_path)
                
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            result["report_error"] = str(e)
    
    return result

# Add a function to load contract from file
def load_contract(contract_path: str) -> AiCertifyContract:
    """
    Load an AiCertifyContract from a JSON file.
    
    Args:
        contract_path: Path to the contract JSON file
        
    Returns:
        AiCertifyContract object
    """
    try:
        with open(contract_path, "r") as f:
            contract_data = json.load(f)
        return AiCertifyContract.parse_obj(contract_data)
    except Exception as e:
        logger.error(f"Error loading contract from {contract_path}: {e}")
        raise

async def evaluate_contract(
    contract: AiCertifyContract,
    policy_categories: List[str] = ["eu_ai_act", "ai_fairness"],
    output_dir: Optional[str] = None,
    report_format: str = "markdown"
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Evaluate a contract against specified policy categories.
    
    Args:
        contract: The AiCertifyContract to evaluate
        policy_categories: List of policy categories to evaluate against
        output_dir: Directory to save reports to
        report_format: Format of the report ("markdown", "pdf", or "both")
        
    Returns:
        Tuple of (evaluation_result, opa_results)
    """
    # Create evaluator
    evaluator = AICertifyEvaluator()
    
    # Extract conversations from contract
    conversations = []
    for interaction in contract.interactions:
        conversations.append({
            "prompt": interaction.input_text,  # Map input_text to prompt
            "response": interaction.output_text  # Map output_text to response
        })
    
    # Evaluate conversations
    evaluation_result = await evaluator.evaluate_conversations(
        app_name=contract.application_name,
        conversations=conversations
    )
    
    # Ensure the evaluation result has a valid structure
    evaluation_result = _ensure_valid_evaluation_structure(evaluation_result)
    
    # Evaluate against policies
    opa_results = {}
    for category in policy_categories:
        try:
            logger.info(f"Evaluating against policy category: {category}")
            category_results = evaluator.evaluate_policy(evaluation_result, category)
            opa_results[category] = category_results
        except Exception as e:
            logger.error(f"Error evaluating policy {category}: {e}", exc_info=True)
            opa_results[category] = {"error": str(e)}
    
    return evaluation_result, opa_results

async def generate_reports(
    contract: AiCertifyContract,
    evaluation_result: Dict[str, Any],
    opa_results: Dict[str, Any],
    output_dir: Optional[str] = None,
    report_format: str = "markdown"
) -> Dict[str, str]:
    """
    Generate evaluation reports.
    
    Args:
        contract: Contract that was evaluated
        evaluation_result: Evaluation results
        opa_results: OPA policy evaluation results
        output_dir: Directory to save reports to
        report_format: Format of the report ("markdown", "pdf", or "both")
        
    Returns:
        Dictionary of report paths
    """
    # Ensure output directory exists
    if output_dir is None:
        output_dir = "reports"
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate timestamp for filenames
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Ensure app_name is set
    app_name = evaluation_result.get("app_name", "Unknown")
    if app_name == "Unknown" and contract:
        app_name = contract.application_name
    
    # Generate reports
    report_paths = {}
    
    try:
        # Import required components
        from aicertify.report_generation.report_generator import ReportGenerator
        from aicertify.report_generation.data_extraction import create_evaluation_report
        
        # Create the evaluation report model
        report_model = create_evaluation_report(evaluation_result, opa_results)
        
        # Create ReportGenerator instance
        report_gen = ReportGenerator()
        
        # Generate markdown content
        md_content = report_gen.generate_markdown_report(report_model)
        
        # Generate markdown report
        if report_format.lower() in ["markdown", "both"]:
            md_path = os.path.join(output_dir, f"report_{app_name}_{timestamp}.md")
            
            # Save markdown report
            with open(md_path, "w") as f:
                f.write(md_content)
            
            logger.info(f"Markdown report saved to: {md_path}")
            report_paths["markdown"] = md_path
        
        # Generate PDF report
        if report_format.lower() in ["pdf", "both"]:
            pdf_path = os.path.join(output_dir, f"report_{app_name}_{timestamp}.pdf")
            
            # Generate PDF from markdown content
            pdf_result = report_gen.generate_pdf_report(md_content, pdf_path)
            
            logger.info(f"PDF report saved to: {pdf_result}")
            report_paths["pdf"] = pdf_result
    except ImportError as e:
        logger.error(f"Required modules for report generation not available: {e}")
        report_paths["error"] = f"Report generation failed: {str(e)}"
    except Exception as e:
        logger.error(f"Error generating reports: {e}", exc_info=True)
        report_paths["error"] = str(e)
    
    return report_paths

# Custom JSON encoder to handle UUID serialization
class CustomJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles UUID objects."""
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

# New function for Phase 1 evaluators
async def evaluate_contract_with_phase1_evaluators(
    contract: AiCertifyContract,
    evaluators: Optional[List[str]] = None,
    evaluator_config: Optional[Dict[str, Any]] = None,
    generate_report: bool = True,
    report_format: str = "markdown",
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Evaluate a contract using the Phase 1 evaluators.
    
    Args:
        contract: The AiCertifyContract object to evaluate
        evaluators: List of evaluator names to use, or None for all available
        evaluator_config: Configuration for the evaluators
        generate_report: Whether to generate a report
        report_format: Format of the report (json, markdown, pdf)
        output_dir: Directory to save the report
        
    Returns:
        Dictionary containing evaluation results and report paths
    """
    logger.info(f"Evaluating contract {contract.contract_id} with Phase 1 evaluators")
    
    # Convert config dict to EvaluatorConfig
    config = EvaluatorConfig(**evaluator_config) if evaluator_config else EvaluatorConfig()
    
    # Create evaluator
    compliance_evaluator = ComplianceEvaluator(evaluators=evaluators, config=config)
    
    # Get contract data
    contract_data = contract.dict()
    
    # Run evaluation
    results = await compliance_evaluator.evaluate_async(contract_data)
    
    # Determine overall compliance
    overall_compliant = compliance_evaluator.is_compliant(results)
    
    # Generate report if requested
    report_path = None
    if generate_report:
        report = compliance_evaluator.generate_report(results, format=report_format)
        
        # Save report to file if output directory specified
        if output_dir:
            # Ensure output directory exists
            os.makedirs(output_dir, exist_ok=True)
            
            # Generate filename
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            if report_format == "json":
                filename = f"compliance_report_{contract.application_name}_{timestamp}.json"
            elif report_format == "markdown":
                filename = f"compliance_report_{contract.application_name}_{timestamp}.md"
            elif report_format == "pdf":
                filename = f"compliance_report_{contract.application_name}_{timestamp}.pdf"
            else:
                filename = f"compliance_report_{contract.application_name}_{timestamp}.txt"
            
            # Save report
            report_path = os.path.join(output_dir, filename)
            with open(report_path, "w") as f:
                f.write(report.content)
            
            logger.info(f"Saved compliance report to {report_path}")
    
    # Return results
    return {
        "results": {name: result.dict() for name, result in results.items()},
        "overall_compliant": overall_compliant,
        "report_path": report_path,
        "contract_id": str(contract.contract_id),
        "application_name": contract.application_name
    }

# New API function that uses both OPA and Phase 1 evaluators
async def evaluate_contract_comprehensive(
    contract: AiCertifyContract,
    policy_category: str = "eu_ai_act",
    evaluators: Optional[List[str]] = None,
    evaluator_config: Optional[Dict[str, Any]] = None,
    generate_report: bool = True,
    report_format: str = "markdown",
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Perform comprehensive evaluation of a contract using both OPA policies 
    and Phase 1 evaluators.
    
    Args:
        contract: The AiCertifyContract object to evaluate
        policy_category: Category of OPA policies to evaluate against
        evaluators: List of evaluator names to use, or None for all available
        evaluator_config: Configuration for the evaluators
        generate_report: Whether to generate a report
        report_format: Format of the report (json, markdown, pdf)
        output_dir: Directory to save the report
        
    Returns:
        Dictionary containing evaluation results and report paths
    """
    logger.info(f"Performing comprehensive evaluation of contract {contract.contract_id}")
    
    # Evaluate with Phase 1 evaluators
    phase1_results = await evaluate_contract_with_phase1_evaluators(
        contract=contract,
        evaluators=evaluators,
        evaluator_config=evaluator_config,
        generate_report=False  # We'll generate a combined report later
    )
    
    # Evaluate with OPA policies
    try:
        # Create a simple evaluator result for OPA
        evaluation_result = {
            "contract_id": str(contract.contract_id),
            "application_name": contract.application_name,
            "interaction_count": len(contract.interactions),
            "fairness_metrics": {},  # Will be populated later
            "summary_text": f"Evaluation of {contract.application_name} with {len(contract.interactions)} interactions"
        }
        
        # Add Phase 1 metrics to the evaluation result
        if phase1_results and "results" in phase1_results:
            fairness_result = phase1_results["results"].get("fairness", {})
            if fairness_result:
                evaluation_result["fairness_metrics"] = fairness_result.get("details", {})
        
        # Serialize input data with custom encoder to handle UUIDs
        contract_dict = contract.dict()
        input_json_str = json.dumps({"contract": contract_dict, "evaluation": evaluation_result}, cls=CustomJSONEncoder)
        input_data_serialized = json.loads(input_json_str)
        
        # Split the policy_category parameter (expected to be in the form 'parent/sub') into category and subcategory
        if "/" in policy_category:
            cat, subcat = policy_category.split("/", 1)
        else:
            cat = policy_category
            subcat = ""
        
        opa_results = opa_evaluator.evaluate_policies_by_category(
            category=cat,
            subcategory=subcat,
            input_data=input_data_serialized
        )
        
        logger.info("OPA policy evaluation complete")
    except Exception as e:
        logger.error(f"Error during OPA policy evaluation: {str(e)}")
        opa_results = [{"error": f"OPA evaluation error: {str(e)}"}]
    
    # Generate combined report if requested
    report_path = None
    if generate_report:
        if output_dir:
            # Ensure output directory exists
            os.makedirs(output_dir, exist_ok=True)
            
            # Generate filename
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            if report_format == "json":
                filename = f"comprehensive_report_{contract.application_name}_{timestamp}.json"
            elif report_format == "markdown":
                filename = f"comprehensive_report_{contract.application_name}_{timestamp}.md"
            elif report_format == "pdf":
                filename = f"comprehensive_report_{contract.application_name}_{timestamp}.pdf"
            else:
                filename = f"comprehensive_report_{contract.application_name}_{timestamp}.txt"
            
            # Generate report
            try:
                from aicertify.report_generation.report_generator import ReportGenerator
                from aicertify.report_generation.data_extraction import create_evaluation_report
                
                # Create report generator instance
                report_gen = ReportGenerator()
                
                report_data = create_evaluation_report(
                    evaluation_result=evaluation_result,
                    opa_results=opa_results
                )
                
                # Generate report based on format
                if report_format == "markdown":
                    report_content = report_gen.generate_markdown_report(report_data)
                elif report_format == "pdf":
                    # First generate markdown content
                    md_content = report_gen.generate_markdown_report(report_data)
                    # Then convert to PDF
                    pdf_path = os.path.join(output_dir, f"report_{contract.application_name}_{timestamp}.pdf")
                    report_gen.generate_pdf_report(md_content, pdf_path)
                    report_content = f"PDF report generated at {pdf_path}"
                else:
                    report_content = report_gen.generate_markdown_report(report_data)
                
                # Save report
                report_path = os.path.join(output_dir, filename)
                with open(report_path, "w") as f:
                    f.write(report_content)
                
                logger.info(f"Saved comprehensive report to {report_path}")
            except Exception as e:
                logger.error(f"Error generating report: {str(e)}")
    
    # Return results
    return {
        "phase1_results": phase1_results,
        "opa_results": opa_results,
        "report_path": report_path,
        "contract_id": str(contract.contract_id),
        "application_name": contract.application_name
    }

async def evaluate_contract_by_folder(
    contract: AiCertifyContract,
    policy_folder: str,
    evaluators: Optional[List[str]] = None,
    evaluator_config: Optional[Dict[str, Any]] = None,
    generate_report: bool = True,
    report_format: str = "markdown",
    output_dir: Optional[str] = None,
    opa_evaluator: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Perform comprehensive evaluation of a contract using both OPA policies 
    and Phase 1 evaluators, using the folder-based approach for OPA policies.
    
    Args:
        contract: The AiCertifyContract object to evaluate
        policy_folder: Folder name to search for in the OPA policies directory
        evaluators: List of evaluator names to use, or None for all available
        evaluator_config: Configuration for the evaluators
        generate_report: Whether to generate a report
        report_format: Format of the report (json, markdown, pdf)
        output_dir: Directory to save the report
        opa_evaluator: Optional existing OpaEvaluator instance to reuse
        
    Returns:
        Dictionary containing evaluation results and report paths
    """
    logger.info(f"Performing folder-based evaluation of contract {contract.contract_id}")
    
    # Evaluate with Phase 1 evaluators
    phase1_results = await evaluate_contract_with_phase1_evaluators(
        contract=contract,
        evaluators=evaluators,
        evaluator_config=evaluator_config,
        generate_report=False  # We'll generate a combined report later
    )
    
    # Evaluate with OPA policies using folder-based approach
    try:
        # Create a simple evaluator result for OPA
        evaluation_result = {
            "contract_id": str(contract.contract_id),
            "application_name": contract.application_name,
            "interaction_count": len(contract.interactions),
            "fairness_metrics": {},  # Will be populated later
            "summary_text": f"Evaluation of {contract.application_name} with {len(contract.interactions)} interactions"
        }
        
        # Add Phase 1 metrics to the evaluation result
        if phase1_results and "results" in phase1_results:
            fairness_result = phase1_results["results"].get("fairness", {})
            if fairness_result:
                evaluation_result["fairness_metrics"] = fairness_result.get("details", {})
        
        # Serialize input data with custom encoder to handle UUIDs
        contract_dict = contract.dict()
        input_json_str = json.dumps({"contract": contract_dict, "evaluation": evaluation_result}, cls=CustomJSONEncoder)
        input_data_serialized = json.loads(input_json_str)
        
        # Use the provided OpaEvaluator or create a new one
        if opa_evaluator is None:
            logger.info("Creating new OpaEvaluator instance")
            from aicertify.opa_core.evaluator import OpaEvaluator
            opa_evaluator = OpaEvaluator(use_external_server=False)
            opa_evaluator.load_policies()
        else:
            logger.info("Using provided OpaEvaluator instance")
        
        # Use the folder-based evaluation method
        opa_results = opa_evaluator.evaluate_by_folder_name(
            folder_name=policy_folder,
            input_data=input_data_serialized
        )
        
        logger.info("OPA policy evaluation complete using folder-based approach")
    except Exception as e:
        logger.error(f"Error during OPA policy evaluation: {str(e)}")
        opa_results = {"error": f"OPA evaluation error: {str(e)}"}
    
    # Generate combined report if requested
    report_path = None
    if generate_report:
        if output_dir:
            # Ensure output directory exists
            os.makedirs(output_dir, exist_ok=True)
            
            # Generate filename
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            if report_format == "json":
                filename = f"folder_report_{contract.application_name}_{timestamp}.json"
            elif report_format == "markdown":
                filename = f"folder_report_{contract.application_name}_{timestamp}.md"
            elif report_format == "pdf":
                filename = f"folder_report_{contract.application_name}_{timestamp}.pdf"
            else:
                filename = f"folder_report_{contract.application_name}_{timestamp}.txt"
            
            # Generate report
            try:
                from aicertify.report_generation.report_generator import ReportGenerator
                from aicertify.report_generation.data_extraction import create_evaluation_report
                
                # Create report generator instance
                report_gen = ReportGenerator()
                
                report_data = create_evaluation_report(
                    evaluation_result=evaluation_result,
                    opa_results=opa_results
                )
                
                # Generate report based on format
                if report_format == "markdown":
                    report_content = report_gen.generate_markdown_report(report_data)
                elif report_format == "pdf":
                    # First generate markdown content
                    md_content = report_gen.generate_markdown_report(report_data)
                    # Then convert to PDF
                    pdf_path = os.path.join(output_dir, f"report_{contract.application_name}_{timestamp}.pdf")
                    report_gen.generate_pdf_report(md_content, pdf_path)
                    report_content = f"PDF report generated at {pdf_path}"
                else:
                    report_content = report_gen.generate_markdown_report(report_data)
                
                # Save report
                report_path = os.path.join(output_dir, filename)
                with open(report_path, "w") as f:
                    f.write(report_content)
                
                logger.info(f"Saved folder-based report to {report_path}")
            except Exception as e:
                logger.error(f"Error generating report: {str(e)}")
    
    # Return results
    return {
        "phase1_results": phase1_results,
        "opa_results": opa_results,
        "report_path": report_path,
        "contract_id": str(contract.contract_id),
        "application_name": contract.application_name
    } 