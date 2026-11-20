"""Background task queue management for batch processing (Phase 3)"""

from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select

from .. import models


class BatchTaskManager:
    """Manager for batch analysis tasks using task queue"""

    @staticmethod
    def create_batch_task(
        db: Session,
        workspace_id: str,
        content_version_ids: list[str],
        created_by_user_id: str,
        task_type: str = "batch",
        analysis_params: dict = None
    ) -> models.AnalysisTask:
        """Create a new batch analysis task"""
        
        if analysis_params is None:
            analysis_params = {
                "compare_scope": "all",
                "use_advanced_analysis": True
            }
        
        # Estimate completion time (roughly 2 seconds per article)
        estimated_duration = len(content_version_ids) * 2
        estimated_completion = datetime.now(timezone.utc) + timedelta(seconds=estimated_duration)
        
        task = models.AnalysisTask(
            id=models.new_id(),
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            task_type=task_type,
            target_ids=content_version_ids,
            analysis_params=analysis_params,
            status="pending",
            progress=0,
            estimated_completion_at=estimated_completion,
        )
        
        db.add(task)
        db.commit()
        db.refresh(task)
        
        return task

    @staticmethod
    def get_task(db: Session, task_id: str) -> models.AnalysisTask:
        """Get a task by ID"""
        return db.scalar(
            select(models.AnalysisTask)
            .where(models.AnalysisTask.id == task_id)
        )

    @staticmethod
    def update_task_progress(
        db: Session,
        task_id: str,
        progress: int,
        completed_analysis_id: str = None
    ):
        """Update task progress"""
        task = BatchTaskManager.get_task(db, task_id)
        if not task:
            return
        
        task.progress = min(progress, 100)
        
        if completed_analysis_id:
            if not task.completed_analysis_ids:
                task.completed_analysis_ids = []
            task.completed_analysis_ids.append(completed_analysis_id)
        
        if progress >= 100:
            task.status = "completed"
            task.completed_at = datetime.now(timezone.utc)
        elif progress > 0:
            task.status = "running"
        
        task.updated_at = datetime.now(timezone.utc)
        db.commit()

    @staticmethod
    def fail_task(db: Session, task_id: str, error_message: str):
        """Mark task as failed"""
        task = BatchTaskManager.get_task(db, task_id)
        if not task:
            return
        
        task.status = "failed"
        task.error_message = error_message
        task.completed_at = datetime.now(timezone.utc)
        task.updated_at = datetime.now(timezone.utc)
        db.commit()

    @staticmethod
    def get_pending_tasks(db: Session, limit: int = 10) -> list[models.AnalysisTask]:
        """Get pending tasks ready to process"""
        return db.scalars(
            select(models.AnalysisTask)
            .where(models.AnalysisTask.status == "pending")
            .order_by(models.AnalysisTask.created_at)
            .limit(limit)
        ).all()

    @staticmethod
    def get_workspace_tasks(
        db: Session,
        workspace_id: str,
        status: str = None,
        limit: int = 20
    ) -> list[models.AnalysisTask]:
        """Get tasks for a workspace"""
        query = select(models.AnalysisTask).where(
            models.AnalysisTask.workspace_id == workspace_id
        )
        
        if status:
            query = query.where(models.AnalysisTask.status == status)
        
        return db.scalars(
            query.order_by(models.AnalysisTask.created_at.desc()).limit(limit)
        ).all()


class AnalysisHistoryManager:
    """Manager for tracking analysis history and improvements"""

    @staticmethod
    def record_analysis(
        db: Session,
        content_version_id: str,
        analysis: models.OriginalityAnalysis
    ):
        """Record analysis in history"""
        
        # Get previous analysis
        previous = db.scalar(
            select(models.AnalysisHistory)
            .where(models.AnalysisHistory.content_version_id == content_version_id)
            .order_by(models.AnalysisHistory.revision.desc())
            .limit(1)
        )
        
        # Calculate revision and score change
        revision = (previous.revision + 1) if previous else 1
        score_change = (analysis.total_score - previous.score_snapshot) if previous else None
        
        # Detect improvements
        improvements = {}
        if previous:
            if analysis.similarity_score > previous.score_snapshot:
                improvements["similarity"] = "Improved relative similarity assessment"
            if analysis.originality_score > previous.score_snapshot:
                improvements["originality"] = "Better originality indicators"
            if analysis.structure_score > previous.score_snapshot:
                improvements["structure"] = "Improved structure organization"
        
        history = models.AnalysisHistory(
            id=models.new_id(),
            content_version_id=content_version_id,
            analysis_id=analysis.id,
            revision=revision,
            score_snapshot=analysis.total_score,
            score_change=score_change,
            improvements=improvements,
        )
        
        db.add(history)
        db.commit()

    @staticmethod
    def get_history(db: Session, content_version_id: str, limit: int = 10) -> list[models.AnalysisHistory]:
        """Get analysis history for a content version"""
        return db.scalars(
            select(models.AnalysisHistory)
            .where(models.AnalysisHistory.content_version_id == content_version_id)
            .order_by(models.AnalysisHistory.created_at.desc())
            .limit(limit)
        ).all()

    @staticmethod
    def calculate_improvement_trend(
        db: Session,
        content_version_id: str,
        periods: int = 5
    ) -> dict:
        """Calculate improvement trend over time"""
        history = AnalysisHistoryManager.get_history(db, content_version_id, limit=periods)
        history = list(reversed(history))  # Chronological order
        
        if not history:
            return {}
        
        trend = {
            "total_analyses": len(history),
            "score_trajectory": [h.score_snapshot for h in history],
            "average_score": sum(h.score_snapshot for h in history) / len(history),
            "max_score": max(h.score_snapshot for h in history),
            "min_score": min(h.score_snapshot for h in history),
            "trend_direction": "improving" if history[-1].score_snapshot > history[0].score_snapshot else "stable" if history[-1].score_snapshot == history[0].score_snapshot else "declining",
        }
        
        return trend


class PerformanceMonitor:
    """Monitor and track system performance"""

    @staticmethod
    def record_analysis_performance(
        db: Session,
        workspace_id: str,
        processing_time_ms: int,
        analysis_type: str = "standard"
    ):
        """Record analysis performance metrics"""
        # Can be extended to store in a dedicated metrics table
        pass

    @staticmethod
    def get_performance_stats(db: Session, workspace_id: str, hours: int = 24) -> dict:
        """Get performance statistics"""
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        
        # Count recent analyses
        recent_analyses = db.execute(
            select(models.OriginalityAnalysis)
            .where(
                models.OriginalityAnalysis.workspace_id == workspace_id,
                models.OriginalityAnalysis.analyzed_at >= cutoff_time
            )
        ).scalars().all()
        
        if not recent_analyses:
            return {
                "total_analyses": 0,
                "average_score": 0,
                "processing_time_avg": 0,
            }
        
        processing_times = []  # Would be populated from actual metrics
        
        stats = {
            "total_analyses": len(recent_analyses),
            "average_score": sum(a.total_score for a in recent_analyses) / len(recent_analyses),
            "level_distribution": {
                "excellent": sum(1 for a in recent_analyses if a.level == "excellent"),
                "good": sum(1 for a in recent_analyses if a.level == "good"),
                "fair": sum(1 for a in recent_analyses if a.level == "fair"),
                "poor": sum(1 for a in recent_analyses if a.level == "poor"),
            },
            "processing_time_avg": sum(processing_times) / len(processing_times) if processing_times else 0,
        }
        
        return stats
