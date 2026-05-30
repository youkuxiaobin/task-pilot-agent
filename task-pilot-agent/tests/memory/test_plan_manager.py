import pytest

pytest.skip(
    "Legacy plan-manager tests reference memory.plan_manager, which is no longer part of the current runtime.",
    allow_module_level=True,
)

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, List
import uuid
from datetime import datetime

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from memory.plan_manager import PlanManager, PlanStep


class TestPlanManager:
    """Test suite for PlanManager class"""
    
    @pytest.fixture
    def mock_settings(self):
        """Mock AgentSettings with database configuration"""
        settings = Mock()
        settings.db = Mock()
        settings.db.dsn = "sqlite:///:memory:"
        return settings
    
    @pytest.fixture
    def plan_manager(self, mock_settings):
        """PlanManager instance with mocked dependencies"""
        with patch('memory.plan_manager.create_engine') as mock_engine_factory:
            mock_engine = Mock()
            mock_engine_factory.return_value = mock_engine
            
            with patch('memory.plan_manager.sessionmaker') as mock_sessionmaker:
                mock_session_class = Mock()
                mock_sessionmaker.return_value = mock_session_class
                
                manager = PlanManager(mock_settings)
                manager.engine = mock_engine
                manager.Session = mock_session_class
                
                yield manager
    
    def test_initialization(self, mock_settings):
        """Test PlanManager initialization with settings"""
        with patch('memory.plan_manager.create_engine') as mock_engine_factory:
            mock_engine = Mock()
            mock_engine_factory.return_value = mock_engine
            
            with patch('memory.plan_manager.sessionmaker') as mock_sessionmaker:
                mock_session_class = Mock()
                mock_sessionmaker.return_value = mock_session_class
                
                with patch('memory.plan_manager.Base.metadata.create_all') as mock_create_tables:
                    manager = PlanManager(mock_settings)
                    
                    assert manager.settings == mock_settings
                    assert manager.engine == mock_engine
                    assert manager.Session == mock_session_class
                    mock_engine_factory.assert_called_once_with(mock_settings.db.dsn)
                    mock_create_tables.assert_called_once_with(mock_engine)
    
    def test_initialization_default_settings(self):
        """Test PlanManager initialization with default settings"""
        with patch('memory.plan_manager.AgentSettings') as mock_settings_class:
            mock_settings = Mock()
            mock_settings.db = Mock()
            mock_settings.db.dsn = "sqlite:///:memory:"
            mock_settings_class.return_value = mock_settings
            
            with patch('memory.plan_manager.create_engine') as mock_engine_factory:
                mock_engine = Mock()
                mock_engine_factory.return_value = mock_engine
                
                with patch('memory.plan_manager.sessionmaker') as mock_sessionmaker:
                    mock_session_class = Mock()
                    mock_sessionmaker.return_value = mock_session_class
                    
                    with patch('memory.plan_manager.Base.metadata.create_all'):
                        manager = PlanManager()
                        
                        assert manager.settings == mock_settings
    
    def test_generate_plan_id(self, plan_manager):
        """Test plan ID generation"""
        plan_id1 = plan_manager._generate_plan_id()
        plan_id2 = plan_manager._generate_plan_id()
        
        # Should be different
        assert plan_id1 != plan_id2
        
        # Should be string
        assert isinstance(plan_id1, str)
        assert isinstance(plan_id2, str)
        
        # Should be numeric (snowflake-like)
        assert plan_id1.isdigit()
        assert plan_id2.isdigit()
    
    def test_add_plan(self, plan_manager):
        """Test adding a new plan"""
        steps = ["step1", "step2", "step3"]
        user_id = "test-user"
        agent_id = "test-agent"
        run_id = "test-run"
        
        mock_session = Mock()
        plan_manager.Session.return_value = mock_session
        
        with patch.object(plan_manager, '_generate_plan_id', return_value="plan-123"):
            result = plan_manager.add_plan(steps, user_id, agent_id, run_id)
            
            assert result == "plan-123"
            assert mock_session.add.call_count == 3
            mock_session.commit.assert_called_once()
            mock_session.close.assert_called_once()
    
    def test_add_plan_exception_handling(self, plan_manager):
        """Test add_plan with exception handling"""
        steps = ["step1", "step2"]
        user_id = "test-user"
        
        mock_session = Mock()
        mock_session.add.side_effect = Exception("Database error")
        plan_manager.Session.return_value = mock_session
        
        with patch.object(plan_manager, '_generate_plan_id', return_value="plan-123"):
            with pytest.raises(Exception, match="Database error"):
                plan_manager.add_plan(steps, user_id)
            
            mock_session.rollback.assert_called_once()
            mock_session.close.assert_called_once()
    
    def test_delete_plan(self, plan_manager):
        """Test deleting a plan"""
        plan_id = "plan-123"
        
        mock_session = Mock()
        mock_session.query.return_value.filter.return_value.delete.return_value = 2
        plan_manager.Session.return_value = mock_session
        
        result = plan_manager.delete_plan(plan_id)
        
        assert result is True
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()
    
    def test_delete_plan_not_found(self, plan_manager):
        """Test deleting a non-existent plan"""
        plan_id = "non-existent"
        
        mock_session = Mock()
        mock_session.query.return_value.filter.return_value.delete.return_value = 0
        plan_manager.Session.return_value = mock_session
        
        result = plan_manager.delete_plan(plan_id)
        
        assert result is False
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()
    
    def test_delete_plan_exception_handling(self, plan_manager):
        """Test delete_plan with exception handling"""
        plan_id = "plan-123"
        
        mock_session = Mock()
        mock_session.query.return_value.filter.return_value.delete.side_effect = Exception("Database error")
        plan_manager.Session.return_value = mock_session
        
        with pytest.raises(Exception, match="Database error"):
            plan_manager.delete_plan(plan_id)
        
        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()
    
    def test_update_plan(self, plan_manager):
        """Test updating a plan"""
        plan_id = "plan-123"
        steps = ["updated step1", "updated step2"]
        user_id = "test-user"
        agent_id = "test-agent"
        run_id = "test-run"
        
        # Mock delete_plan to return True
        with patch.object(plan_manager, 'delete_plan', return_value=True):
            mock_session = Mock()
            plan_manager.Session.return_value = mock_session
            
            result = plan_manager.update_plan(plan_id, steps, user_id, agent_id, run_id)
            
            assert result is True
            assert mock_session.add.call_count == 2
            mock_session.commit.assert_called_once()
            mock_session.close.assert_called_once()
    
    def test_update_plan_delete_fails(self, plan_manager):
        """Test update_plan when delete fails"""
        plan_id = "plan-123"
        steps = ["updated step1"]
        
        with patch.object(plan_manager, 'delete_plan', return_value=False):
            result = plan_manager.update_plan(plan_id, steps)
            
            assert result is False
    
    def test_update_plan_exception_handling(self, plan_manager):
        """Test update_plan with exception handling"""
        plan_id = "plan-123"
        steps = ["updated step1"]
        
        with patch.object(plan_manager, 'delete_plan', return_value=True):
            mock_session = Mock()
            mock_session.add.side_effect = Exception("Database error")
            plan_manager.Session.return_value = mock_session
            
            with pytest.raises(Exception, match="Database error"):
                plan_manager.update_plan(plan_id, steps)
            
            mock_session.rollback.assert_called_once()
            mock_session.close.assert_called_once()
    
    def test_get_plan_by_plan_id(self, plan_manager):
        """Test retrieving plan by plan ID"""
        plan_id = "plan-123"
        
        mock_step1 = Mock()
        mock_step1.plan_id = plan_id
        mock_step1.step_content = "step1"
        mock_step1.user_id = "user1"
        mock_step1.agent_id = "agent1"
        mock_step1.run_id = "run1"
        mock_step1.created_at = datetime.now()
        mock_step1.updated_at = datetime.now()
        
        mock_step2 = Mock()
        mock_step2.plan_id = plan_id
        mock_step2.step_content = "step2"
        mock_step2.user_id = "user1"
        mock_step2.agent_id = "agent1"
        mock_step2.run_id = "run1"
        mock_step2.created_at = datetime.now()
        mock_step2.updated_at = datetime.now()
        
        mock_session = Mock()
        mock_query = Mock()
        mock_query.filter.return_value.order_by.return_value.all.return_value = [mock_step1, mock_step2]
        mock_session.query.return_value = mock_query
        plan_manager.Session.return_value = mock_session
        
        result = plan_manager.get_plan(plan_id=plan_id)
        
        assert len(result) == 2
        assert result[0]['plan_id'] == plan_id
        assert result[0]['step_content'] == "step1"
        assert result[1]['step_content'] == "step2"
        mock_session.close.assert_called_once()
    
    def test_get_plan_by_user_id(self, plan_manager):
        """Test retrieving plan by user ID"""
        user_id = "user1"
        
        mock_step = Mock()
        mock_step.plan_id = "plan-123"
        mock_step.step_content = "step1"
        mock_step.user_id = user_id
        mock_step.agent_id = "agent1"
        mock_step.run_id = "run1"
        mock_step.created_at = datetime.now()
        mock_step.updated_at = datetime.now()
        
        mock_session = Mock()
        mock_query = Mock()
        mock_query.filter.return_value.order_by.return_value.all.return_value = [mock_step]
        mock_session.query.return_value = mock_query
        plan_manager.Session.return_value = mock_session
        
        result = plan_manager.get_plan(user_id=user_id)
        
        assert len(result) == 1
        assert result[0]['user_id'] == user_id
        mock_session.close.assert_called_once()
    
    def test_get_plan_by_agent_id(self, plan_manager):
        """Test retrieving plan by agent ID"""
        agent_id = "agent1"
        
        mock_step = Mock()
        mock_step.plan_id = "plan-123"
        mock_step.step_content = "step1"
        mock_step.user_id = "user1"
        mock_step.agent_id = agent_id
        mock_step.run_id = "run1"
        mock_step.created_at = datetime.now()
        mock_step.updated_at = datetime.now()
        
        mock_session = Mock()
        mock_query = Mock()
        mock_query.filter.return_value.order_by.return_value.all.return_value = [mock_step]
        mock_session.query.return_value = mock_query
        plan_manager.Session.return_value = mock_session
        
        result = plan_manager.get_plan(agent_id=agent_id)
        
        assert len(result) == 1
        assert result[0]['agent_id'] == agent_id
        mock_session.close.assert_called_once()
    
    def test_get_plan_by_run_id(self, plan_manager):
        """Test retrieving plan by run ID"""
        run_id = "run1"
        
        mock_step = Mock()
        mock_step.plan_id = "plan-123"
        mock_step.step_content = "step1"
        mock_step.user_id = "user1"
        mock_step.agent_id = "agent1"
        mock_step.run_id = run_id
        mock_step.created_at = datetime.now()
        mock_step.updated_at = datetime.now()
        
        mock_session = Mock()
        mock_query = Mock()
        mock_query.filter.return_value.order_by.return_value.all.return_value = [mock_step]
        mock_session.query.return_value = mock_query
        plan_manager.Session.return_value = mock_session
        
        result = plan_manager.get_plan(run_id=run_id)
        
        assert len(result) == 1
        assert result[0]['run_id'] == run_id
        mock_session.close.assert_called_once()
    
    def test_get_plan_multiple_filters(self, plan_manager):
        """Test retrieving plan with multiple filters"""
        user_id = "user1"
        agent_id = "agent1"
        run_id = "run1"
        
        mock_step = Mock()
        mock_step.plan_id = "plan-123"
        mock_step.step_content = "step1"
        mock_step.user_id = user_id
        mock_step.agent_id = agent_id
        mock_step.run_id = run_id
        mock_step.created_at = datetime.now()
        mock_step.updated_at = datetime.now()
        
        mock_session = Mock()
        mock_query = Mock()
        mock_query.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_step]
        mock_session.query.return_value = mock_query
        plan_manager.Session.return_value = mock_session
        
        result = plan_manager.get_plan(user_id=user_id, agent_id=agent_id, run_id=run_id)
        
        assert len(result) == 1
        assert result[0]['user_id'] == user_id
        assert result[0]['agent_id'] == agent_id
        assert result[0]['run_id'] == run_id
        mock_session.close.assert_called_once()
    
    def test_get_plan_no_filters(self, plan_manager):
        """Test retrieving plan without filters"""
        mock_step = Mock()
        mock_step.plan_id = "plan-123"
        mock_step.step_content = "step1"
        mock_step.user_id = "user1"
        mock_step.agent_id = "agent1"
        mock_step.run_id = "run1"
        mock_step.created_at = datetime.now()
        mock_step.updated_at = datetime.now()
        
        mock_session = Mock()
        mock_query = Mock()
        mock_query.order_by.return_value.all.return_value = [mock_step]
        mock_session.query.return_value = mock_query
        plan_manager.Session.return_value = mock_session
        
        result = plan_manager.get_plan()
        
        assert len(result) == 1
        mock_session.close.assert_called_once()
    
    def test_get_plan_exception_handling(self, plan_manager):
        """Test get_plan with exception handling"""
        mock_session = Mock()
        mock_session.query.side_effect = Exception("Database error")
        plan_manager.Session.return_value = mock_session
        
        with pytest.raises(Exception, match="Database error"):
            plan_manager.get_plan()
        
        mock_session.close.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
