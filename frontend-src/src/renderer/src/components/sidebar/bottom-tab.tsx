/* eslint-disable */
import { Tabs } from '@chakra-ui/react'
import { FiMonitor, FiGlobe } from 'react-icons/fi'
import { useTranslation } from 'react-i18next'
import { sidebarStyles } from './sidebar-styles'
import ScreenPanel from './screen-panel'
import BrowserPanel from './browser-panel'
import {
  OptionalSidebarContent,
  OptionalSidebarTrigger,
  useOptionalFeatureAvailability,
} from '@optional-feature'

function BottomTab(): JSX.Element {
  const { t } = useTranslation();
  const optionalFeatureAvailable = useOptionalFeatureAvailability();
  
  return (
    <Tabs.Root 
      defaultValue={optionalFeatureAvailable ? "optional-feature" : "screen"}
      variant="plain"
      {...sidebarStyles.bottomTab.container}
    >
      <Tabs.List {...sidebarStyles.bottomTab.list}>
        {optionalFeatureAvailable && <OptionalSidebarTrigger />}
        <Tabs.Trigger value="screen" {...sidebarStyles.bottomTab.trigger}>
          <FiMonitor />
          {t('sidebar.screen')}
        </Tabs.Trigger>
        <Tabs.Trigger value="browser" {...sidebarStyles.bottomTab.trigger}>
          <FiGlobe />
          {t('sidebar.browser')}
        </Tabs.Trigger>
      </Tabs.List>

      {optionalFeatureAvailable && <OptionalSidebarContent />}
      
      <Tabs.Content value="screen">
        <ScreenPanel />
      </Tabs.Content>
      
      <Tabs.Content value="browser">
        <BrowserPanel />
      </Tabs.Content>
    </Tabs.Root>
  );
}

export default BottomTab
